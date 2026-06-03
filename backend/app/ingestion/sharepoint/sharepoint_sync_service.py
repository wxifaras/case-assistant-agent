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
import uuid
from typing import Any, Protocol

import httpx

from app.api.schemas.sharepoint import (
    SharePointMultiSiteSyncRequest,
    SharePointMultiSiteSyncResponse,
    SharePointSyncRequest,
    SharePointSyncResponse,
)
from app.core.logger import Logger
from app.core.settings import Settings
from app.ingestion.sharepoint.file_sync_runner import SharePointFileSyncRunner
from app.ingestion.sharepoint.graph_adapter import IGraphAdapterFactory
from app.ingestion.sharepoint.state_store import SharePointSyncStateStore
from app.ingestion.sharepoint.transfer_service import SharePointTransferService
from app.models.sharepoint import SharePointFileSyncStateItem
from app.repositories.cosmos_repository import CosmosRepository
from app.services.blob_storage_service import BlobStorageService


class ISharePointSyncService(Protocol):
    """Interface for the SharePoint -> Blob sync service."""

    async def get_sites(
        self, *, search: str = "*", max_results: int = 200, include_libraries: bool = False
    ) -> list[dict[str, Any]]:
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

    async def sync_site(
        self,
        request: SharePointSyncRequest,
        delegated_graph_access_token: str | None = None,
    ) -> SharePointSyncResponse:
        """Run a delta-aware sync for a single SharePoint site."""
        ...

    async def sync(
        self,
        request: SharePointMultiSiteSyncRequest,
        delegated_graph_access_token: str | None = None,
    ) -> SharePointMultiSiteSyncResponse:
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
        graph_adapter_factory: IGraphAdapterFactory,
        state_store: SharePointSyncStateStore,
        file_sync_runner: SharePointFileSyncRunner,
        transfer_service: SharePointTransferService,
        sites_repo: CosmosRepository | None = None,
    ) -> None:
        self._settings = settings
        self._blob_service = blob_service
        self._logger = logger
        self._sites_repo = sites_repo
        self._http: httpx.AsyncClient | None = None
        self._factory = graph_adapter_factory
        self._state_store = state_store
        self._file_sync_runner = file_sync_runner
        self._transfer_service = transfer_service

    # ------------------------------------------------------------------ #
    # lifecycle                                                          #
    # ------------------------------------------------------------------ #

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            timeout = httpx.Timeout(self._settings.sharepoint.request_timeout_seconds)
            self._http = httpx.AsyncClient(timeout=timeout)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        await self._factory.close()

    # ------------------------------------------------------------------ #
    # public entry point                                                 #
    # ------------------------------------------------------------------ #

    async def sync_site(
        self,
        request: SharePointSyncRequest,
        delegated_graph_access_token: str | None = None,
    ) -> SharePointSyncResponse:
        """Run a delta-aware sync from SharePoint to Blob storage."""
        started = time.monotonic()
        warnings: list[str] = []

        site_hostname, site_path = self._resolve_site(request)
        library_name = request.library_name or self._settings.sharepoint.library_name
        container = self._resolve_container(request)
        tenant_id = self._resolve_tenant_id(request.tenant_id)

        delegated_token = (delegated_graph_access_token or "").strip() or None
        adapter = await self._factory.create_async(delegated_token)

        site_id, resolved_site_name = await adapter.resolve_site_info(site_hostname, site_path)
        drive_id = await adapter.resolve_drive_id(request, site_id, library_name)
        folder_item_id = await adapter.resolve_folder_item_id(drive_id, request.folder_path)

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

        batch_result = await self._file_sync_runner.process_files(
            iter_files=adapter.iter_files(drive_id, folder_item_id),
            previous_states=previous_states,
            tenant_id=tenant_id,
            site_id=site_id,
            drive_id=drive_id,
            container=container,
            prefix=prefix,
            site_name=site_name,
            site_case_code=site_case_code,
            library_name=resolved_library,
            headers={},
            build_sharepoint_metadata=self._build_sharepoint_metadata,
            copy_download_url_to_blob=self._copy_download_url_to_blob,
            copy_drive_item_to_blob=self._make_copy_drive_item_fn(adapter),
        )
        warnings.extend(batch_result.warnings)

        deleted = await self._reconcile_deleted_files(
            tenant_id=tenant_id,
            site_id=site_id,
            current_item_ids=batch_result.current_item_ids,
            previous_states=previous_states,
            container=container,
            expected_prefix=prefix,
        )
        await self._persist_file_states(batch_result.current_state_items)

        elapsed = time.monotonic() - started
        self._logger.info(
            f"SharePoint sync done: discovered={batch_result.discovered} copied={batch_result.copied} "
            f"skipped={batch_result.skipped} failed={batch_result.failed} "
            f"added={batch_result.added} updated={batch_result.updated} unchanged={batch_result.unchanged} "
            f"deleted={deleted} elapsed={elapsed:.2f}s"
        )
        return SharePointSyncResponse(
            discovered=batch_result.discovered,
            copied=batch_result.copied,
            skipped=batch_result.skipped,
            failed=batch_result.failed,
            elapsed_seconds=round(elapsed, 3),
            destination_container=container,
            items=batch_result.items,
            warnings=warnings,
            added=batch_result.added,
            updated=batch_result.updated,
            unchanged=batch_result.unchanged,
            deleted=deleted,
        )

    async def get_sites(
        self, *, search: str = "*", max_results: int = 200, include_libraries: bool = False
    ) -> list[dict[str, Any]]:
        adapter = await self._factory.create_async(None)
        return await adapter.get_sites(search=search, max_results=max_results, include_libraries=include_libraries)

    async def get_member_sites(
        self,
        *,
        user_id: str,
        search: str = "*",
        max_results: int = 200,
        tenant_id: str | None = None,
    ) -> list[dict[str, str]]:
        """List user site memberships directly from Graph (real-time)."""
        adapter = await self._factory.create_async(None)
        return await adapter.get_member_sites(
            user_id=user_id,
            search=search,
            max_results=max_results,
            tenant_id=self._resolve_tenant_id(tenant_id),
        )

    async def get_site_members(
        self,
        *,
        site_hostname: str,
        site_path: str,
        tenant_id: str | None = None,
    ) -> list[dict[str, str]]:
        """List site members directly from Graph for real-time membership checks."""
        adapter = await self._factory.create_async(None)
        return await adapter.get_site_members(
            site_hostname=site_hostname,
            site_path=site_path,
            tenant_id=self._resolve_tenant_id(tenant_id),
        )

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #

    async def sync(
        self,
        request: SharePointMultiSiteSyncRequest,
        delegated_graph_access_token: str | None = None,
    ) -> SharePointMultiSiteSyncResponse:
        """Run sync sequentially for multiple sites."""
        results: list[SharePointSyncResponse] = []
        errors: list[str] = []
        succeeded_sites = 0
        tenant_id = self._resolve_tenant_id(request.tenant_id)

        for site_request in request.sites:
            effective_request = site_request.model_copy(update={"tenant_id": tenant_id})
            try:
                result = await self.sync_site(
                    effective_request,
                    delegated_graph_access_token=delegated_graph_access_token,
                )
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

    @classmethod
    def _new_id(cls) -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _build_state_id(existing_id: str | None = None) -> str:
        return existing_id or SharePointSyncService._new_id()

    @staticmethod
    def _build_site_id(existing_id: str | None = None) -> str:
        return existing_id or SharePointSyncService._new_id()

    async def _load_previous_file_states(
        self,
        *,
        tenant_id: str,
        site_id: str,
        drive_id: str,
        container: str,
        prefix: str,
    ) -> dict[str, SharePointFileSyncStateItem]:
        return await self._state_store.load_previous_file_states(
            tenant_id=tenant_id,
            site_id=site_id,
            drive_id=drive_id,
            container=container,
            prefix=prefix,
        )

    async def _persist_file_states(self, states: list[SharePointFileSyncStateItem]) -> None:
        await self._state_store.persist_file_states(states)

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
        return await self._state_store.reconcile_deleted_files(
            tenant_id=tenant_id,
            site_id=site_id,
            current_item_ids=current_item_ids,
            previous_states=previous_states,
            container=container,
            expected_prefix=expected_prefix,
        )

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
        await self._state_store.upsert_site_doc(
            tenant_id=tenant_id,
            site_id=site_id,
            site_hostname=site_hostname,
            site_path=site_path,
            site_name=site_name,
            drive_id=drive_id,
            library_name=library_name,
        )

    def _build_sharepoint_metadata(
        self,
        site_name: str,
        case_code: str,
        library_name: str,
        site_id: str,
        drive_id: str,
        file_entry: dict[str, Any],
    ) -> dict[str, str]:
        return self._transfer_service.build_sharepoint_metadata(
            site_name=site_name,
            case_code=case_code,
            library_name=library_name,
            site_id=site_id,
            drive_id=drive_id,
            file_entry=file_entry,
        )

    async def _copy_download_url_to_blob(
        self,
        download_url: str,
        container: str,
        blob_name: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Stream a pre-signed Azure Storage URL into Blob Storage (no Graph auth)."""
        await self._transfer_service.copy_download_url_to_blob(
            client=self._ensure_http(),
            download_url=download_url,
            container=container,
            blob_name=blob_name,
            metadata=metadata,
        )

    def _make_copy_drive_item_fn(self, adapter: Any) -> Any:
        """Return a closure that downloads a drive item via *adapter* and copies to Blob."""

        async def _copy(
            headers: dict[str, str],  # kept for API compatibility with FileSyncRunner
            drive_id: str,
            item_id: str,
            container: str,
            blob_name: str,
            metadata: dict[str, str] | None = None,
        ) -> None:
            content = await adapter.download_file_content(drive_id, item_id)
            await self._blob_service.upload_artifact(
                container=container,
                blob_name=blob_name,
                data=content,
                metadata=metadata,
            )

        return _copy
