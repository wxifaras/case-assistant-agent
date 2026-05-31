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

import re
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.api.schemas.sharepoint import (
    SharePointMultiSiteSyncRequest,
    SharePointMultiSiteSyncResponse,
    SharePointSyncItemResult,
    SharePointSyncRequest,
    SharePointSyncResponse,
)
from app.core.logger import Logger
from app.core.settings import Settings
from app.models.sharepoint import SharePointFileSyncStateItem, SharePointSiteItem, SharePointSiteMemberItem
from app.repositories.cosmos_repository import CosmosRepository
from app.services.blob_storage_service import BlobStorageService


class ISharePointSyncService(Protocol):
    """Interface for the SharePoint -> Blob sync service."""

    async def get_sites(self, *, search: str = "*", max_results: int = 200) -> list[dict[str, str]]:
        """Return SharePoint sites visible to the app via Graph search."""
        ...

    async def get_member_sites(
        self,
        *,
        user_id: str,
        search: str = "*",
        max_results: int = 200,
        tenant_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Return sites where the user is a member, resolved live from Graph."""
        ...

    async def get_site_members(
        self,
        *,
        site_hostname: str,
        site_path: str,
        tenant_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Return site members directly from Graph for real-time checks."""
        ...

    async def sync_site(self, request: SharePointSyncRequest) -> SharePointSyncResponse:
        """Run a delta-aware sync for a single SharePoint site."""
        ...

    async def sync(self, request: SharePointMultiSiteSyncRequest) -> SharePointMultiSiteSyncResponse:
        """Run sync sequentially for multiple site requests."""
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
        sites_repo: CosmosRepository | None = None,
    ) -> None:
        self._settings = settings
        self._blob_service = blob_service
        self._logger = logger
        self._sites_repo = sites_repo
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

    async def sync_site(self, request: SharePointSyncRequest) -> SharePointSyncResponse:
        """Run a delta-aware sync from SharePoint to Blob storage."""
        started = time.monotonic()
        warnings: list[str] = []

        site_hostname, site_path = self._resolve_site(request)
        library_name = request.library_name or self._settings.sharepoint.library_name
        container = self._resolve_container(request)
        tenant_id = self._resolve_tenant_id(request.tenant_id)

        token = await self._acquire_graph_token()
        headers = {
            "Authorization": f"Bearer {token}",
            # Required for Graph $search queries used by group-member fallback.
            "ConsistencyLevel": "eventual",
        }

        site_id, resolved_site_name = await self._resolve_site_info(headers, site_hostname, site_path)
        drive_id = await self._resolve_drive_id(headers, request, site_id, library_name)
        folder_item_id = await self._resolve_folder_item_id(headers, drive_id, request.folder_path)

        # Build base site metadata for all files
        site_path_name = site_path.rstrip("/").rsplit("/", 1)[-1]
        site_name = resolved_site_name or site_path_name
        site_case_code = self._extract_case_code(site_name)
        resolved_library = library_name or request.drive_id or "documents"

        # Blob prefix is derived from site path and library to avoid cross-site collisions.
        prefix = f"{site_path_name}/{resolved_library}/"

        self._logger.info(
            f"SharePoint sync starting: site={site_hostname}{site_path} "
            f"library={resolved_library} folder={request.folder_path or '/'} "
            f"container={container} prefix={prefix}"
        )

        await self._upsert_site_doc(
            tenant_id=tenant_id,
            site_id=site_id,
            site_hostname=site_hostname,
            site_path=site_path,
            site_name=site_name,
            drive_id=drive_id,
            library_name=resolved_library,
        )

        previous_states = await self._load_previous_file_states(
            tenant_id=tenant_id,
            site_id=site_id,
            drive_id=drive_id,
            container=container,
            prefix=prefix,
        )

        items: list[SharePointSyncItemResult] = []
        copied = skipped = failed = discovered = 0
        added = updated = unchanged = 0
        current_item_ids: set[str] = set()
        current_state_items: list[SharePointFileSyncStateItem] = []

        async for file_entry in self._iter_files(headers, drive_id, folder_item_id):
            discovered += 1

            source_path = file_entry["_source_path"]
            size = file_entry.get("size")
            item_id = str(file_entry.get("id") or "")
            if not item_id:
                warnings.append(f"Skipping item without id at path '{source_path}'")
                skipped += 1
                continue

            current_item_ids.add(item_id)
            blob_name = f"{prefix}{file_entry['_relative_path']}"
            prior_state = previous_states.get(item_id)

            if prior_state is None:
                delta_status = "added"
                added += 1
            elif self._is_file_updated(prior_state, file_entry):
                delta_status = "updated"
                updated += 1
            else:
                delta_status = "unchanged"
                unchanged += 1

            if delta_status == "unchanged":
                current_state_items.append(
                    self._build_file_state_item(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        drive_id=drive_id,
                        item_id=item_id,
                        file_entry=file_entry,
                        blob_name=blob_name,
                        deleted=False,
                        existing_id=prior_state.id if prior_state is not None else None,
                    )
                )
                skipped += 1
                items.append(
                    SharePointSyncItemResult(
                        source_path=source_path,
                        blob_name=blob_name,
                        size_bytes=size,
                        status="skipped",
                        reason="unchanged",
                    )
                )
                continue

            try:
                # Build SharePoint metadata for blob annotation
                metadata = self._build_sharepoint_metadata(
                    site_name=site_name,
                    case_code=site_case_code,
                    library_name=resolved_library,
                    site_id=site_id,
                    drive_id=drive_id,
                    file_entry=file_entry,
                )

                download_url = file_entry.get("@microsoft.graph.downloadUrl")
                if download_url:
                    await self._copy_download_url_to_blob(download_url, container, blob_name, metadata)
                else:
                    item_id = file_entry.get("id")
                    if not item_id:
                        raise RuntimeError("missing both @microsoft.graph.downloadUrl and driveItem id")
                    await self._copy_drive_item_to_blob(headers, drive_id, item_id, container, blob_name, metadata)

                copied += 1
                items.append(
                    SharePointSyncItemResult(
                        source_path=source_path,
                        blob_name=blob_name,
                        size_bytes=size,
                        status="copied",
                        reason=delta_status,
                    )
                )
                current_state_items.append(
                    self._build_file_state_item(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        drive_id=drive_id,
                        item_id=item_id,
                        file_entry=file_entry,
                        blob_name=blob_name,
                        deleted=False,
                        existing_id=prior_state.id if prior_state is not None else None,
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

        deleted = await self._reconcile_deleted_files(
            tenant_id=tenant_id,
            site_id=site_id,
            current_item_ids=current_item_ids,
            previous_states=previous_states,
            container=container,
            expected_prefix=prefix,
        )
        await self._persist_file_states(current_state_items)

        elapsed = time.monotonic() - started
        self._logger.info(
            f"SharePoint sync done: discovered={discovered} copied={copied} "
            f"skipped={skipped} failed={failed} added={added} updated={updated} unchanged={unchanged} "
            f"deleted={deleted} elapsed={elapsed:.2f}s"
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
            added=added,
            updated=updated,
            unchanged=unchanged,
            deleted=deleted,
        )

    async def get_sites(self, *, search: str = "*", max_results: int = 200) -> list[dict[str, str]]:
        """List SharePoint sites discoverable via Graph ``/sites?search=...``.

        Args:
            search: Graph search expression. Defaults to ``*``.
            max_results: Maximum number of sites to return across pages.
        """
        token = await self._acquire_graph_token()
        headers = {
            "Authorization": f"Bearer {token}",
            # Required for Graph $search.
            "ConsistencyLevel": "eventual",
        }

        base = self._settings.sharepoint.graph_base_url
        normalized_search = self._normalize_sites_search(search)
        encoded_search = urllib.parse.quote(normalized_search)
        url: str | None = f"{base}/sites?search={encoded_search}&$select=id,displayName,webUrl"

        sites: list[dict[str, str]] = []
        while url and len(sites) < max_results:
            payload = await self._graph_get(url, headers)
            for site in payload.get("value", []):
                sites.append(
                    {
                        "id": str(site.get("id") or ""),
                        "displayName": str(site.get("displayName") or ""),
                        "webUrl": str(site.get("webUrl") or ""),
                    }
                )
                if len(sites) >= max_results:
                    break
            url = payload.get("@odata.nextLink")

        return sites

    @staticmethod
    def _normalize_sites_search(search: str | None) -> str:
        """Normalize route input to Graph-compatible site search syntax.

        Graph site search behaves like prefix matching in practice. To keep
        route behavior user-friendly, plain terms are converted to `term*`.
        """
        value = (search or "").strip()
        if not value:
            return "*"
        if value == "*":
            return value
        if "*" in value:
            return value
        return f"{value}*"

    async def get_member_sites(
        self,
        *,
        user_id: str,
        search: str = "*",
        max_results: int = 200,
        tenant_id: str | None = None,
    ) -> list[dict[str, str]]:
        """List user site memberships directly from Graph (real-time)."""
        identifier = (user_id or "").strip()
        if not identifier:
            raise ValueError("user_id is required.")

        # Keep tenant validation behavior aligned with other SharePoint routes.
        _ = self._resolve_tenant_id(tenant_id)
        token = await self._acquire_graph_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
        }

        sites = await self.get_sites(search=search, max_results=max_results)
        member_sites: list[dict[str, str]] = []
        for site in sites:
            site_id = str(site.get("id") or "").strip()
            if not site_id:
                continue

            group_id = await self._resolve_connected_group_id(headers=headers, site_id=site_id)
            if not group_id:
                continue
            is_member = await self._is_user_member_of_group(headers=headers, group_id=group_id, user_id=identifier)
            if not is_member:
                continue

            member_sites.append(
                {
                    "site_id": site_id,
                    "site_name": str(site.get("displayName") or ""),
                    "web_url": str(site.get("webUrl") or ""),
                }
            )

        member_sites.sort(key=lambda item: (item.get("site_name") or "", item.get("site_id") or ""))
        return member_sites

    async def get_site_members(
        self,
        *,
        site_hostname: str,
        site_path: str,
        tenant_id: str | None = None,
    ) -> list[dict[str, str]]:
        """List site members directly from Graph for real-time membership checks."""
        hostname = (site_hostname or "").strip()
        path = (site_path or "").strip()
        if not hostname or not path:
            raise ValueError("site_hostname and site_path are required.")

        resolved_tenant_id = self._resolve_tenant_id(tenant_id)
        token = await self._acquire_graph_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
        }

        site_id, _ = await self._resolve_site_info(headers, hostname, path)
        members = await self._fetch_site_members_from_connected_group(
            headers=headers,
            tenant_id=resolved_tenant_id,
            site_id=site_id,
            existing_members={},
        )
        members.sort(key=lambda item: (item.display_name or "", item.member_id or ""))

        return [
            {
                "member_id": member.member_id,
                "display_name": member.display_name,
                "email": member.email,
                "role": member.role,
                "source": member.source,
            }
            for member in members
        ]

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #

    async def sync(self, request: SharePointMultiSiteSyncRequest) -> SharePointMultiSiteSyncResponse:
        """Run sync sequentially for multiple sites."""
        results: list[SharePointSyncResponse] = []
        errors: list[str] = []
        succeeded_sites = 0
        tenant_id = self._resolve_tenant_id(request.tenant_id)

        for site_request in request.sites:
            effective_request = site_request.model_copy(update={"tenant_id": tenant_id})
            try:
                result = await self.sync_site(effective_request)
                results.append(result)
                succeeded_sites += 1
            except Exception as exc:
                site_hint = f"{site_request.site_hostname or '?'}{site_request.site_path or '?'}"
                message = f"{site_hint}: {exc}"
                self._logger.warning(f"SharePoint multi-sync site failed: {message}")
                errors.append(message)

        return SharePointMultiSiteSyncResponse(
            tenant_id=tenant_id,
            total_sites=len(request.sites),
            succeeded_sites=succeeded_sites,
            failed_sites=len(request.sites) - succeeded_sites,
            results=results,
            errors=errors,
        )

    def _resolve_tenant_id(self, request_tenant_id: str | None) -> str:
        tenant_id = (request_tenant_id or self._settings.azure_tenant_id or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required in request or AZURE_TENANT_ID must be configured.")
        return tenant_id

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

    async def _acquire_graph_token(self) -> str:
        credential = self._ensure_credential()
        token = await credential.get_token(self._settings.sharepoint.graph_scope)
        return token.token

    async def _resolve_drive_id(
        self,
        headers: dict[str, str],
        request: SharePointSyncRequest,
        site_id: str,
        library_name: str | None,
    ) -> str:
        if request.drive_id:
            return request.drive_id
        if not library_name:
            raise ValueError("Either drive_id or library_name must be provided.")

        base = self._settings.sharepoint.graph_base_url
        url = f"{base}/sites/{site_id}/drives"
        data = await self._graph_get(url, headers)
        for drive in data.get("value", []):
            if drive.get("name") == library_name:
                drive_id = drive.get("id")
                if drive_id:
                    return drive_id
        raise ValueError(f"Library '{library_name}' not found on resolved site '{site_id}'.")

    async def _resolve_site_info(self, headers: dict[str, str], hostname: str, site_path: str) -> tuple[str, str]:
        base = self._settings.sharepoint.graph_base_url
        path = site_path if site_path.startswith("/") else f"/{site_path}"
        url = f"{base}/sites/{hostname}:{path}"
        data = await self._graph_get(url, headers)

        site_id = data.get("id")
        if not site_id:
            raise ValueError(f"Could not resolve SharePoint site at {hostname}{path}.")

        site_display_name = str(data.get("displayName") or data.get("name") or "").strip()
        return site_id, site_display_name

    @staticmethod
    def _extract_case_code(site_name: str) -> str:
        """Extract trailing case code from site name (for example ``KM01``)."""
        if not site_name:
            return ""

        match = re.search(r"([A-Za-z]{1,10}[-_ ]?\d{2,})\s*$", site_name.strip())
        if not match:
            return ""

        token = re.sub(r"[-_ ]", "", match.group(1))
        return token.upper()

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
                "?$select=id,name,size,eTag,lastModifiedDateTime,folder,file,parentReference,@microsoft.graph.downloadUrl"
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

    @classmethod
    def _new_id(cls) -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _build_state_id(existing_id: str | None = None) -> str:
        return existing_id or SharePointSyncService._new_id()

    @staticmethod
    def _build_site_id(existing_id: str | None = None) -> str:
        return existing_id or SharePointSyncService._new_id()

    @staticmethod
    def _build_member_id(existing_id: str | None = None) -> str:
        return existing_id or SharePointSyncService._new_id()

    def _build_file_state_item(
        self,
        *,
        tenant_id: str,
        site_id: str,
        drive_id: str,
        item_id: str,
        file_entry: dict[str, Any],
        blob_name: str,
        deleted: bool,
        existing_id: str | None = None,
    ) -> SharePointFileSyncStateItem:
        return SharePointFileSyncStateItem(
            id=self._build_state_id(existing_id),
            tenant_id=tenant_id,
            site_id=site_id,
            drive_id=drive_id,
            item_id=item_id,
            file_name=str(file_entry.get("name") or ""),
            relative_path=str(file_entry.get("_relative_path") or ""),
            blob_name=blob_name,
            etag=str(file_entry.get("eTag") or ""),
            last_modified_utc=str(file_entry.get("lastModifiedDateTime") or ""),
            size_bytes=int(file_entry.get("size") or 0),
            deleted=deleted,
            managed_by_sync=True,
        )

    @staticmethod
    def _is_file_updated(previous_state: SharePointFileSyncStateItem, current_file: dict[str, Any]) -> bool:
        current_etag = str(current_file.get("eTag") or "")
        if current_etag and previous_state.etag:
            return current_etag != previous_state.etag

        current_last_modified = str(current_file.get("lastModifiedDateTime") or "")
        current_size = int(current_file.get("size") or 0)
        return current_last_modified != previous_state.last_modified_utc or current_size != previous_state.size_bytes

    async def _load_previous_file_states(
        self,
        *,
        tenant_id: str,
        site_id: str,
        drive_id: str,
        container: str,
        prefix: str,
    ) -> dict[str, SharePointFileSyncStateItem]:
        states: dict[str, SharePointFileSyncStateItem] = {}
        if self._sites_repo is not None:
            query = (
                "SELECT * FROM c WHERE c.tenant_id = @tenant_id AND c.site_id = @site_id "
                "AND c.doc_type = 'sync_file_state' AND c.drive_id = @drive_id "
                "AND (NOT IS_DEFINED(c.deleted) OR c.deleted = false) ORDER BY c.last_synced_utc DESC"
            )
            params = [
                {"name": "@tenant_id", "value": tenant_id},
                {"name": "@site_id", "value": site_id},
                {"name": "@drive_id", "value": drive_id},
            ]
            async for item in self._sites_repo.query_items(query, parameters=params):
                state = SharePointFileSyncStateItem.model_validate(item)
                # Keep newest record per SharePoint item_id when legacy and GUID ids coexist.
                if state.item_id not in states:
                    states[state.item_id] = state

        if states:
            return states

        # Bootstrap mode for initial migration: recover prior state from blob metadata.
        list_with_metadata = getattr(self._blob_service, "list_blob_items_with_metadata", None)
        if list_with_metadata is None:
            return states

        try:
            blob_items = await list_with_metadata(container=container, prefix=prefix)
        except TypeError:
            return states

        for blob in blob_items:
            metadata = blob.get("metadata") or {}
            item_id = str(metadata.get("sp_item_id") or "")
            if not item_id:
                continue
            states[item_id] = SharePointFileSyncStateItem(
                id=self._build_state_id(),
                tenant_id=tenant_id,
                site_id=site_id,
                drive_id=drive_id,
                item_id=item_id,
                file_name=str(metadata.get("sp_filename") or ""),
                relative_path=str(metadata.get("sp_file_path") or ""),
                blob_name=str(blob.get("name") or ""),
                etag=str(metadata.get("sp_etag") or ""),
                last_modified_utc=str(metadata.get("sp_last_modified_utc") or ""),
                size_bytes=int(metadata.get("sp_file_size_bytes") or 0),
                deleted=str(metadata.get("metadata_storage_file_deleted") or "false").lower() == "true",
                managed_by_sync=str(metadata.get("sp_sync_managed") or "false").lower() == "true",
            )

        return states

    async def _persist_file_states(self, states: list[SharePointFileSyncStateItem]) -> None:
        if self._sites_repo is None:
            return
        for item in states:
            await self._sites_repo.upsert_item(item.model_dump(mode="json"))

    async def _reconcile_deleted_files(
        self,
        *,
        tenant_id: str,
        site_id: str,
        current_item_ids: set[str],
        previous_states: dict[str, SharePointFileSyncStateItem],
        container: str,
        expected_prefix: str,
    ) -> int:
        deleted_count = 0
        deleted_items: list[SharePointFileSyncStateItem] = []
        for item_id, prior in previous_states.items():
            if item_id in current_item_ids:
                continue
            if not prior.managed_by_sync:
                continue
            if not prior.blob_name.startswith(expected_prefix):
                continue

            metadata = await self._blob_service.get_blob_metadata(container=container, blob_name=prior.blob_name)
            if metadata is None:
                continue
            if str(metadata.get("sp_sync_managed") or "false").lower() != "true":
                continue

            metadata["metadata_storage_file_deleted"] = "true"
            await self._blob_service.set_blob_metadata(
                container=container, blob_name=prior.blob_name, metadata=metadata
            )

            deleted_count += 1
            deleted_items.append(prior.model_copy(update={"deleted": True, "last_synced_utc": datetime.now(UTC)}))

        await self._persist_file_states(deleted_items)
        return deleted_count

    async def _upsert_site_doc(
        self,
        *,
        tenant_id: str,
        site_id: str,
        site_hostname: str,
        site_path: str,
        site_name: str,
        drive_id: str,
        library_name: str,
    ) -> None:
        if self._sites_repo is None:
            return
        existing = await self._load_site_doc(tenant_id=tenant_id, site_id=site_id)
        site_item = SharePointSiteItem(
            id=self._build_site_id(existing.id if existing is not None else None),
            tenant_id=tenant_id,
            site_id=site_id,
            site_hostname=site_hostname,
            site_path=site_path,
            site_name=site_name,
            drive_id=drive_id,
            library_name=library_name,
        )
        await self._sites_repo.upsert_item(site_item.model_dump(mode="json"))

    async def _load_site_doc(self, *, tenant_id: str, site_id: str) -> SharePointSiteItem | None:
        if self._sites_repo is None:
            return None
        query = (
            "SELECT TOP 1 * FROM c WHERE c.tenant_id = @tenant_id AND c.site_id = @site_id "
            "AND c.doc_type = 'site' ORDER BY c.last_synced_utc DESC"
        )
        params = [{"name": "@tenant_id", "value": tenant_id}, {"name": "@site_id", "value": site_id}]
        async for item in self._sites_repo.query_items(query, parameters=params):
            return SharePointSiteItem.model_validate(item)
        return None

    async def _fetch_site_members_from_connected_group(
        self,
        *,
        headers: dict[str, str],
        tenant_id: str,
        site_id: str,
        existing_members: dict[str, SharePointSiteMemberItem],
    ) -> list[SharePointSiteMemberItem]:
        group_id = await self._resolve_connected_group_id(headers=headers, site_id=site_id)
        if not group_id:
            return []

        owner_ids = await self._load_group_owner_ids(headers=headers, group_id=group_id)
        members: list[SharePointSiteMemberItem] = []

        base = self._settings.sharepoint.graph_base_url
        url: str | None = f"{base}/groups/{group_id}/transitiveMembers" "?$select=id,displayName,userPrincipalName,mail"
        while url:
            payload = await self._graph_get(url, headers)
            for principal in payload.get("value", []):
                member_id = str(principal.get("id") or "").strip()
                if not member_id:
                    continue

                display_name = str(
                    principal.get("displayName")
                    or principal.get("userPrincipalName")
                    or principal.get("mail")
                    or member_id
                )
                email = str(principal.get("mail") or principal.get("userPrincipalName") or "")
                principal_type = str(principal.get("@odata.type") or "").split(".")[-1] or "principal"
                role = "owner" if member_id in owner_ids else "member"

                existing_member = existing_members.get(member_id)
                members.append(
                    SharePointSiteMemberItem(
                        id=self._build_member_id(existing_member.id if existing_member is not None else None),
                        tenant_id=tenant_id,
                        site_id=site_id,
                        member_id=member_id,
                        display_name=display_name,
                        email=email,
                        role=role,
                        source=f"graph-group-transitive-{principal_type}",
                    )
                )

            url = payload.get("@odata.nextLink")

        return members

    async def _resolve_connected_group_id(self, *, headers: dict[str, str], site_id: str) -> str | None:
        base = self._settings.sharepoint.graph_base_url
        site = await self._graph_get(f"{base}/sites/{site_id}?$select=displayName", headers)
        site_display_name = str(site.get("displayName") or "").strip()
        if not site_display_name:
            return None

        search_query = urllib.parse.quote(f'"displayName:{site_display_name}"')
        groups = await self._graph_get(f"{base}/groups?$search={search_query}&$select=id,displayName", headers)
        group_values = groups.get("value") or []
        if not group_values:
            return None

        # Prefer exact display-name match; otherwise use first search result.
        exact = next(
            (g for g in group_values if str(g.get("displayName") or "").strip().lower() == site_display_name.lower()),
            None,
        )
        target = exact or group_values[0]
        return str(target.get("id") or "") or None

    async def _load_group_owner_ids(self, *, headers: dict[str, str], group_id: str) -> set[str]:
        base = self._settings.sharepoint.graph_base_url
        url: str | None = f"{base}/groups/{group_id}/owners?$select=id"
        owner_ids: set[str] = set()

        while url:
            payload = await self._graph_get(url, headers)
            for owner in payload.get("value", []):
                owner_id = str(owner.get("id") or "").strip()
                if owner_id:
                    owner_ids.add(owner_id)
            url = payload.get("@odata.nextLink")

        return owner_ids

    async def _is_user_member_of_group(self, *, headers: dict[str, str], group_id: str, user_id: str) -> bool:
        base = self._settings.sharepoint.graph_base_url
        identifier = str(user_id or "").strip().lower()
        if not identifier:
            return False

        # Some tenants reject $filter on transitiveMembers.
        # Enumerate and compare locally for id/upn/mail.
        url: str | None = (
            f"{base}/groups/{group_id}/transitiveMembers/microsoft.graph.user" "?$select=id,userPrincipalName,mail"
        )
        while url:
            payload = await self._graph_get(url, headers)
            for member in payload.get("value", []):
                member_id = str(member.get("id") or "").strip().lower()
                member_upn = str(member.get("userPrincipalName") or "").strip().lower()
                member_mail = str(member.get("mail") or "").strip().lower()
                if identifier in {member_id, member_upn, member_mail}:
                    return True
            url = payload.get("@odata.nextLink")

        return False

    def _build_sharepoint_metadata(
        self,
        site_name: str,
        case_code: str,
        library_name: str,
        site_id: str,
        drive_id: str,
        file_entry: dict[str, Any],
    ) -> dict[str, str]:
        """Build SharePoint metadata dict for blob annotation.

        Blob metadata carries two categories:
                * Search index fields (sp_site_name, sp_case_code, sp_library_name,
                    sp_last_modified_utc, sp_filename, sp_file_path, sp_file_size_bytes)
                    — projected into Azure AI
          Search for filtering and ranking.
        * ``metadata_storage_file_deleted`` — watched by the indexer's
          SoftDeleteColumnDeletionDetectionPolicy; set to ``"true"`` by the
          reconciliation pass to remove orphaned documents from the index.

        Delta-tracking fields are persisted in Cosmos and mirrored in blob
        metadata for bootstrap/recovery safety.
        """
        file_size = file_entry.get("size")
        file_size_str = str(file_size) if file_size is not None else "0"

        return {
            "metadata_storage_file_deleted": "false",
            "sp_sync_managed": "true",
            "sp_item_id": str(file_entry.get("id") or ""),
            "sp_etag": str(file_entry.get("eTag") or ""),
            "sp_site_id": site_id,
            "sp_drive_id": drive_id,
            # --- search index fields ---
            "sp_site_name": site_name,
            "sp_case_code": case_code,
            "sp_library_name": library_name,
            "sp_last_modified_utc": file_entry.get("lastModifiedDateTime", ""),
            "sp_filename": file_entry.get("name", ""),
            "sp_file_path": file_entry.get("_relative_path", ""),
            "sp_file_size_bytes": file_size_str,
        }

    async def _copy_download_url_to_blob(
        self,
        download_url: str,
        container: str,
        blob_name: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        await self._stream_to_blob(download_url, container, blob_name, metadata=metadata)

    async def _copy_drive_item_to_blob(
        self,
        headers: dict[str, str],
        drive_id: str,
        item_id: str,
        container: str,
        blob_name: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        base = self._settings.sharepoint.graph_base_url
        url = f"{base}/drives/{drive_id}/items/{item_id}/content"
        await self._stream_to_blob(url, container, blob_name, headers=headers, follow_redirects=True, metadata=metadata)

    async def _stream_to_blob(
        self,
        url: str,
        container: str,
        blob_name: str,
        *,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
        metadata: dict[str, str] | None = None,
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
                metadata=metadata,
                overwrite=True,
            )
