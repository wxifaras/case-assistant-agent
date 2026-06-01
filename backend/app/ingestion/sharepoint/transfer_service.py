from __future__ import annotations

from typing import Any

import httpx

from app.services.blob_storage_service import BlobStorageService


class SharePointTransferService:
    """Build blob metadata and stream SharePoint content into Blob Storage."""

    def __init__(
        self,
        *,
        blob_service: BlobStorageService,
        graph_base_url: str,
        download_chunk_size_bytes: int,
    ) -> None:
        self._blob_service = blob_service
        self._graph_base_url = graph_base_url
        self._download_chunk_size_bytes = download_chunk_size_bytes

    def build_sharepoint_metadata(
        self,
        *,
        site_name: str,
        case_code: str,
        library_name: str,
        site_id: str,
        drive_id: str,
        file_entry: dict[str, Any],
    ) -> dict[str, str]:
        """Build SharePoint metadata dict for blob annotation."""
        file_size = file_entry.get("size")
        file_size_str = str(file_size) if file_size is not None else "0"

        return {
            "metadata_storage_file_deleted": "false",
            "sp_sync_managed": "true",
            "sp_item_id": str(file_entry.get("id") or ""),
            "sp_etag": str(file_entry.get("eTag") or ""),
            "sp_site_id": site_id,
            "sp_drive_id": drive_id,
            "sp_site_name": site_name,
            "sp_case_code": case_code,
            "sp_library_name": library_name,
            "sp_last_modified_utc": file_entry.get("lastModifiedDateTime", ""),
            "sp_filename": file_entry.get("name", ""),
            "sp_file_path": file_entry.get("_relative_path", ""),
            "sp_file_size_bytes": file_size_str,
        }

    async def copy_download_url_to_blob(
        self,
        *,
        client: httpx.AsyncClient,
        download_url: str,
        container: str,
        blob_name: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        await self._stream_to_blob(
            client=client,
            url=download_url,
            container=container,
            blob_name=blob_name,
            metadata=metadata,
        )

    async def copy_drive_item_to_blob(
        self,
        *,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        drive_id: str,
        item_id: str,
        container: str,
        blob_name: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        url = f"{self._graph_base_url}/drives/{drive_id}/items/{item_id}/content"
        await self._stream_to_blob(
            client=client,
            url=url,
            container=container,
            blob_name=blob_name,
            headers=headers,
            follow_redirects=True,
            metadata=metadata,
        )

    async def _stream_to_blob(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        container: str,
        blob_name: str,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> None:
        async with client.stream("GET", url, headers=headers, follow_redirects=follow_redirects) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"File download failed with {response.status_code}: {response.text[:500]}")

            content_length = response.headers.get("Content-Length")
            total_size = int(content_length) if content_length and content_length.isdigit() else None

            await self._blob_service.upload_artifact_stream(
                container=container,
                blob_name=blob_name,
                data=response.aiter_bytes(chunk_size=self._download_chunk_size_bytes),
                length=total_size,
                metadata=metadata,
                overwrite=True,
            )
