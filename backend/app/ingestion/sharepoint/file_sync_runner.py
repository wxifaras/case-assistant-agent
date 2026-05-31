from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from app.api.schemas.sharepoint import SharePointSyncItemResult
from app.core.logger import Logger
from app.models.sharepoint import SharePointFileSyncStateItem


class SharePointFileSyncBatchResult(BaseModel):
    """In-memory result of processing one SharePoint site file batch."""

    items: list[SharePointSyncItemResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    current_item_ids: set[str] = Field(default_factory=set)
    current_state_items: list[SharePointFileSyncStateItem] = Field(default_factory=list)
    discovered: int = 0
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0


class SharePointFileSyncRunner:
    """Process SharePoint drive items into blob-sync actions and state records."""

    def __init__(
        self,
        *,
        logger: Logger,
        build_state_id: Callable[[str | None], str],
    ) -> None:
        self._logger = logger
        self._build_state_id = build_state_id

    async def process_files(
        self,
        *,
        iter_files: AsyncIterator[dict[str, Any]],
        previous_states: dict[str, SharePointFileSyncStateItem],
        tenant_id: str,
        site_id: str,
        drive_id: str,
        container: str,
        prefix: str,
        site_name: str,
        site_case_code: str,
        library_name: str,
        headers: dict[str, str],
        build_sharepoint_metadata: Callable[..., dict[str, str]],
        copy_download_url_to_blob: Callable[..., Awaitable[None]],
        copy_drive_item_to_blob: Callable[..., Awaitable[None]],
    ) -> SharePointFileSyncBatchResult:
        result = SharePointFileSyncBatchResult()

        async for file_entry in iter_files:
            result.discovered += 1

            source_path = file_entry["_source_path"]
            size = file_entry.get("size")
            item_id = str(file_entry.get("id") or "")
            if not item_id:
                result.warnings.append(f"Skipping item without id at path '{source_path}'")
                result.skipped += 1
                continue

            result.current_item_ids.add(item_id)
            blob_name = f"{prefix}{file_entry['_relative_path']}"
            prior_state = previous_states.get(item_id)
            delta_status = self._resolve_delta_status(result, prior_state, file_entry)

            if delta_status == "unchanged":
                result.current_state_items.append(
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
                result.skipped += 1
                result.items.append(
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
                metadata = build_sharepoint_metadata(
                    site_name=site_name,
                    case_code=site_case_code,
                    library_name=library_name,
                    site_id=site_id,
                    drive_id=drive_id,
                    file_entry=file_entry,
                )

                download_url = file_entry.get("@microsoft.graph.downloadUrl")
                if download_url:
                    await copy_download_url_to_blob(download_url, container, blob_name, metadata)
                else:
                    drive_item_id = str(file_entry.get("id") or "")
                    if not drive_item_id:
                        raise RuntimeError("missing both @microsoft.graph.downloadUrl and driveItem id")
                    await copy_drive_item_to_blob(headers, drive_id, drive_item_id, container, blob_name, metadata)

                result.copied += 1
                result.items.append(
                    SharePointSyncItemResult(
                        source_path=source_path,
                        blob_name=blob_name,
                        size_bytes=size,
                        status="copied",
                        reason=delta_status,
                    )
                )
                result.current_state_items.append(
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
                result.failed += 1
                result.items.append(
                    SharePointSyncItemResult(
                        source_path=source_path,
                        size_bytes=size,
                        status="failed",
                        reason=str(exc),
                    )
                )

        return result

    def _resolve_delta_status(
        self,
        result: SharePointFileSyncBatchResult,
        previous_state: SharePointFileSyncStateItem | None,
        file_entry: dict[str, Any],
    ) -> str:
        if previous_state is None:
            result.added += 1
            return "added"
        if self._is_file_updated(previous_state, file_entry):
            result.updated += 1
            return "updated"

        result.unchanged += 1
        return "unchanged"

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
