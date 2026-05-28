"""SharePoint -> Blob Storage sync service.

Copies files from a SharePoint document library into Azure Blob Storage
to stage them for downstream indexing.

Auth uses ``DefaultAzureCredential`` against Microsoft Graph. The identity
must have the application permissions ``Sites.Read.All`` and
``Files.Read.All`` granted with admin consent. Writes to Blob Storage reuse
the existing :class:`~app.services.blob_storage_service.BlobStorageService`,
which authenticates via the same credential chain.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Protocol

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.api.schemas.sharepoint import (
    SharePointSyncItemResult,
    SharePointSyncRequest,
    SharePointSyncResponse,
)
from app.core.logger import Logger
from app.core.settings import Settings
from app.services.blob_storage_service import BlobStorageService


class ISharePointSyncService(Protocol):
    """Interface for the SharePoint -> Blob sync service."""

    async def sync(self, request: SharePointSyncRequest) -> SharePointSyncResponse:
        """Run a copy-only sync from SharePoint to Blob storage."""
        ...

    async def close(self) -> None:
        """Release Graph client and credential resources."""
        ...


class SharePointSyncService:
    """Copy files from a SharePoint document library into Blob Storage.

    The service is created as a singleton; the underlying ``httpx.AsyncClient``
    and ``DefaultAzureCredential`` are initialised lazily on first use and
    released through :meth:`close`.
    """

    def __init__(
        self,
        settings: Settings,
        blob_service: BlobStorageService,
        logger: Logger,
    ) -> None:
        self._settings = settings
        self._blob_service = blob_service
        self._logger = logger
        self._credential: DefaultAzureCredential | None = None
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ #
    # lifecycle                                                          #
    # ------------------------------------------------------------------ #

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            timeout = httpx.Timeout(self._settings.sharepoint.request_timeout_seconds)
            self._http = httpx.AsyncClient(timeout=timeout)
        return self._http

    def _ensure_credential(self) -> DefaultAzureCredential:
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._credential is not None:
            await self._credential.close()
            self._credential = None

    # ------------------------------------------------------------------ #
    # public entry point                                                 #
    # ------------------------------------------------------------------ #

    async def sync(self, request: SharePointSyncRequest) -> SharePointSyncResponse:
        """Run a copy-only sync from SharePoint to Blob storage."""
        started = time.monotonic()
        warnings: list[str] = []

        site_hostname, site_path = self._resolve_site(request)
        library_name = request.library_name or self._settings.sharepoint.library_name
        container = self._resolve_container(request)
        cap = self._resolve_cap(request)
        prefix = self._normalize_prefix(request.blob_prefix)

        self._logger.info(
            f"SharePoint sync starting: site={site_hostname}{site_path} "
            f"library={library_name or request.drive_id} folder={request.folder_path or '/'} "
            f"container={container} dry_run={request.dry_run} cap={cap}"
        )

        token = await self._acquire_graph_token()
        headers = {"Authorization": f"Bearer {token}"}

        drive_id = await self._resolve_drive_id(headers, request, site_hostname, site_path, library_name)
        folder_item_id = await self._resolve_folder_item_id(headers, drive_id, request.folder_path)

        items: list[SharePointSyncItemResult] = []
        copied = skipped = failed = discovered = 0

        async for file_entry in self._iter_files(headers, drive_id, folder_item_id):
            if discovered >= cap:
                warnings.append(f"Max files cap reached ({cap}); remaining items not processed")
                break
            discovered += 1

            source_path = file_entry["_source_path"]
            size = file_entry.get("size")
            blob_name = f"{prefix}{file_entry['_relative_path']}"

            if request.dry_run:
                items.append(
                    SharePointSyncItemResult(
                        source_path=source_path,
                        blob_name=blob_name,
                        size_bytes=size,
                        status="dry_run",
                    )
                )
                skipped += 1
                continue

            try:
                download_url = file_entry.get("@microsoft.graph.downloadUrl")
                if download_url:
                    await self._copy_download_url_to_blob(download_url, container, blob_name)
                else:
                    item_id = file_entry.get("id")
                    if not item_id:
                        raise RuntimeError("missing both @microsoft.graph.downloadUrl and driveItem id")
                    await self._copy_drive_item_to_blob(headers, drive_id, item_id, container, blob_name)

                copied += 1
                items.append(
                    SharePointSyncItemResult(
                        source_path=source_path,
                        blob_name=blob_name,
                        size_bytes=size,
                        status="copied",
                    )
                )
            except Exception as exc:
                self._logger.warning(f"Failed to copy '{source_path}': {exc}")
                failed += 1
                items.append(
                    SharePointSyncItemResult(
                        source_path=source_path,
                        size_bytes=size,
                        status="failed",
                        reason=str(exc),
                    )
                )

        elapsed = time.monotonic() - started
        self._logger.info(
            f"SharePoint sync done: discovered={discovered} copied={copied} "
            f"skipped={skipped} failed={failed} elapsed={elapsed:.2f}s"
        )
        return SharePointSyncResponse(
            discovered=discovered,
            copied=copied,
            skipped=skipped,
            failed=failed,
            elapsed_seconds=round(elapsed, 3),
            destination_container=container,
            items=items,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #

    def _resolve_container(self, request: SharePointSyncRequest) -> str:
        container = (
            request.destination_container
            or self._settings.sharepoint.default_blob_container
            or self._settings.blob_storage.container_name
        )
        if not container:
            raise ValueError("No destination blob container configured.")
        return container

    def _resolve_site(self, request: SharePointSyncRequest) -> tuple[str, str]:
        hostname = request.site_hostname or self._settings.sharepoint.site_hostname
        path = request.site_path or self._settings.sharepoint.site_path
        if not hostname or not path:
            raise ValueError(
                "site_hostname and site_path are required in payload unless SHAREPOINT_SITE_HOSTNAME and "
                "SHAREPOINT_SITE_PATH are configured."
            )
        return hostname, path

    def _resolve_cap(self, request: SharePointSyncRequest) -> int:
        hard_cap = self._settings.sharepoint.max_files_per_run
        if request.max_files is None:
            return hard_cap
        return min(request.max_files, hard_cap)

    @staticmethod
    def _normalize_prefix(prefix: str | None) -> str:
        if not prefix:
            return ""
        prefix = prefix.strip().lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return prefix

    async def _acquire_graph_token(self) -> str:
        credential = self._ensure_credential()
        token = await credential.get_token(self._settings.sharepoint.graph_scope)
        return token.token

    async def _resolve_drive_id(
        self,
        headers: dict[str, str],
        request: SharePointSyncRequest,
        site_hostname: str,
        site_path: str,
        library_name: str | None,
    ) -> str:
        if request.drive_id:
            return request.drive_id
        if not library_name:
            raise ValueError("Either drive_id or library_name must be provided.")

        site_id = await self._resolve_site_id(headers, site_hostname, site_path)
        base = self._settings.sharepoint.graph_base_url
        url = f"{base}/sites/{site_id}/drives"
        data = await self._graph_get(url, headers)
        for drive in data.get("value", []):
            if drive.get("name") == library_name:
                return drive["id"]
        raise ValueError(f"Library '{library_name}' not found on site '{site_hostname}{site_path}'.")

    async def _resolve_site_id(self, headers: dict[str, str], hostname: str, site_path: str) -> str:
        base = self._settings.sharepoint.graph_base_url
        path = site_path if site_path.startswith("/") else f"/{site_path}"
        url = f"{base}/sites/{hostname}:{path}"
        data = await self._graph_get(url, headers)
        site_id = data.get("id")
        if not site_id:
            raise ValueError(f"Could not resolve SharePoint site at {hostname}{path}.")
        return site_id

    async def _resolve_folder_item_id(
        self,
        headers: dict[str, str],
        drive_id: str,
        folder_path: str | None,
    ) -> str:
        base = self._settings.sharepoint.graph_base_url
        if not folder_path:
            url = f"{base}/drives/{drive_id}/root"
        else:
            encoded = urllib.parse.quote(folder_path.strip("/"))
            url = f"{base}/drives/{drive_id}/root:/{encoded}"
        data = await self._graph_get(url, headers)
        item_id = data.get("id")
        if not item_id:
            raise ValueError(f"Folder '{folder_path or '/'}' not found in drive {drive_id}.")
        return item_id

    async def _iter_files(
        self,
        headers: dict[str, str],
        drive_id: str,
        folder_item_id: str,
    ):
        """Depth-first iterate file driveItems under ``folder_item_id``.

        Yields raw Graph ``driveItem`` dicts augmented with:
        * ``_source_path``  -- full SharePoint path for logging
        * ``_relative_path`` -- path relative to the starting folder, used for blob naming
        """
        base = self._settings.sharepoint.graph_base_url
        # stack entries: (graph_item_id, relative_prefix)
        stack: list[tuple[str, str]] = [(folder_item_id, "")]
        while stack:
            current_id, rel_prefix = stack.pop()
            url: str | None = (
                f"{base}/drives/{drive_id}/items/{current_id}/children"
                "?$select=id,name,size,folder,file,parentReference,@microsoft.graph.downloadUrl"
            )
            while url:
                data = await self._graph_get(url, headers)
                for entry in data.get("value", []):
                    name = entry.get("name", "")
                    rel_path = f"{rel_prefix}{name}" if not rel_prefix else f"{rel_prefix}{name}"
                    parent_path = (entry.get("parentReference") or {}).get("path", "")
                    source_path = f"{parent_path}/{name}" if parent_path else name
                    if "folder" in entry:
                        stack.append((entry["id"], f"{rel_path}/"))
                        continue
                    if "file" not in entry:
                        # Unknown item kind; skip.
                        continue
                    entry["_source_path"] = source_path
                    entry["_relative_path"] = rel_path
                    yield entry
                url = data.get("@odata.nextLink")

    async def _graph_get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        client = self._ensure_http()
        response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"Graph GET {url} failed with {response.status_code}: {response.text[:500]}")
        return response.json()

    async def _download(self, download_url: str) -> bytes:
        client = self._ensure_http()
        response = await client.get(download_url)
        if response.status_code >= 400:
            raise RuntimeError(f"File download failed with {response.status_code}: {response.text[:500]}")
        return response.content

    async def _download_drive_item(self, headers: dict[str, str], drive_id: str, item_id: str) -> bytes:
        base = self._settings.sharepoint.graph_base_url
        url = f"{base}/drives/{drive_id}/items/{item_id}/content"
        client = self._ensure_http()
        response = await client.get(url, headers=headers, follow_redirects=True)
        if response.status_code >= 400:
            raise RuntimeError(f"Drive item content download failed with {response.status_code}: {response.text[:500]}")
        return response.content

    async def _copy_download_url_to_blob(self, download_url: str, container: str, blob_name: str) -> None:
        await self._stream_to_blob(download_url, container, blob_name)

    async def _copy_drive_item_to_blob(
        self,
        headers: dict[str, str],
        drive_id: str,
        item_id: str,
        container: str,
        blob_name: str,
    ) -> None:
        base = self._settings.sharepoint.graph_base_url
        url = f"{base}/drives/{drive_id}/items/{item_id}/content"
        await self._stream_to_blob(url, container, blob_name, headers=headers, follow_redirects=True)

    async def _stream_to_blob(
        self,
        url: str,
        container: str,
        blob_name: str,
        *,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
    ) -> None:
        client = self._ensure_http()
        chunk_size = self._settings.sharepoint.download_chunk_size_bytes

        async with client.stream("GET", url, headers=headers, follow_redirects=follow_redirects) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"File download failed with {response.status_code}: {response.text[:500]}")

            content_length = response.headers.get("Content-Length")
            total_size = int(content_length) if content_length and content_length.isdigit() else None

            await self._blob_service.upload_artifact_stream(
                container=container,
                blob_name=blob_name,
                data=response.aiter_bytes(chunk_size=chunk_size),
                length=total_size,
                overwrite=True,
            )
