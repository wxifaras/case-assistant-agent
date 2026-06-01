from __future__ import annotations

import urllib.parse
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.api.schemas.sharepoint import SharePointSyncRequest


class SharePointGraphClient:
    """Graph-specific SharePoint navigation and file discovery operations."""

    def __init__(
        self,
        *,
        graph_base_url: str,
    ) -> None:
        self._graph_base_url = graph_base_url

    async def resolve_drive_id(
        self,
        *,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
        request: SharePointSyncRequest,
        site_id: str,
        library_name: str | None,
    ) -> str:
        if request.drive_id:
            return request.drive_id
        if not library_name:
            raise ValueError("Either drive_id or library_name must be provided.")

        url = f"{self._graph_base_url}/sites/{site_id}/drives"
        data = await graph_get(url, headers)
        for drive in data.get("value", []):
            if drive.get("name") == library_name:
                drive_id = drive.get("id")
                if drive_id:
                    return drive_id
        raise ValueError(f"Library '{library_name}' not found on resolved site '{site_id}'.")

    async def resolve_site_info(
        self,
        *,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
        hostname: str,
        site_path: str,
    ) -> tuple[str, str]:
        path = site_path if site_path.startswith("/") else f"/{site_path}"
        url = f"{self._graph_base_url}/sites/{hostname}:{path}"
        data = await graph_get(url, headers)

        site_id = data.get("id")
        if not site_id:
            raise ValueError(f"Could not resolve SharePoint site at {hostname}{path}.")

        site_display_name = str(data.get("displayName") or data.get("name") or "").strip()
        return site_id, site_display_name

    async def resolve_folder_item_id(
        self,
        *,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
        drive_id: str,
        folder_path: str | None,
    ) -> str:
        if not folder_path:
            url = f"{self._graph_base_url}/drives/{drive_id}/root"
        else:
            encoded = urllib.parse.quote(folder_path.strip("/"))
            url = f"{self._graph_base_url}/drives/{drive_id}/root:/{encoded}"
        data = await graph_get(url, headers)
        item_id = data.get("id")
        if not item_id:
            raise ValueError(f"Folder '{folder_path or '/'}' not found in drive {drive_id}.")
        return item_id

    async def iter_files(
        self,
        *,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
        drive_id: str,
        folder_item_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        stack: list[tuple[str, str]] = [(folder_item_id, "")]
        while stack:
            current_id, rel_prefix = stack.pop()
            url: str | None = (
                f"{self._graph_base_url}/drives/{drive_id}/items/{current_id}/children"
                "?$select=id,name,size,eTag,lastModifiedDateTime,folder,file,parentReference,@microsoft.graph.downloadUrl"
            )
            while url:
                data = await graph_get(url, headers)
                for entry in data.get("value", []):
                    name = entry.get("name", "")
                    rel_path = f"{rel_prefix}{name}" if not rel_prefix else f"{rel_prefix}{name}"
                    parent_path = (entry.get("parentReference") or {}).get("path", "")
                    source_path = f"{parent_path}/{name}" if parent_path else name
                    if "folder" in entry:
                        stack.append((entry["id"], f"{rel_path}/"))
                        continue
                    if "file" not in entry:
                        continue
                    entry["_source_path"] = source_path
                    entry["_relative_path"] = rel_path
                    yield entry
                url = data.get("@odata.nextLink")
