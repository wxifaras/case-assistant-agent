from __future__ import annotations

from datetime import UTC, datetime

from app.models.sharepoint import SharePointFileSyncStateItem, SharePointSiteItem
from app.repositories.cosmos_repository import CosmosRepository
from app.services.blob_storage_service import BlobStorageService


class SharePointSyncStateStore:
    """Persist and reconcile SharePoint sync state across runs."""

    def __init__(
        self,
        *,
        blob_service: BlobStorageService,
        sites_repo: CosmosRepository | None,
        build_state_id,
        build_site_id,
    ) -> None:
        self._blob_service = blob_service
        self._sites_repo = sites_repo
        self._build_state_id = build_state_id
        self._build_site_id = build_site_id

    async def load_previous_file_states(
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
                if state.item_id not in states:
                    states[state.item_id] = state

        if states:
            return states

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

    async def persist_file_states(self, states: list[SharePointFileSyncStateItem]) -> None:
        if self._sites_repo is None:
            return
        for item in states:
            await self._sites_repo.upsert_item(item.model_dump(mode="json"))

    async def reconcile_deleted_files(
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

        await self.persist_file_states(deleted_items)
        return deleted_count

    async def upsert_site_doc(
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
        existing = await self.load_site_doc(tenant_id=tenant_id, site_id=site_id)
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

    async def load_site_doc(self, *, tenant_id: str, site_id: str) -> SharePointSiteItem | None:
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
