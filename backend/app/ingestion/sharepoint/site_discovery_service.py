from __future__ import annotations

import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any


class SharePointSiteDiscoveryService:
    """Site discovery operations backed by Microsoft Graph search."""

    def __init__(self, *, graph_base_url: str) -> None:
        self._graph_base_url = graph_base_url

    async def get_sites(
        self,
        *,
        search: str,
        max_results: int,
        include_libraries: bool,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        acquire_graph_token: Callable[[], Awaitable[str]],
    ) -> list[dict[str, Any]]:
        token = await acquire_graph_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
        }

        normalized_search = self.normalize_sites_search(search)
        encoded_search = urllib.parse.quote(normalized_search)
        url: str | None = f"{self._graph_base_url}/sites?search={encoded_search}&$select=id,displayName,webUrl"

        sites: list[dict[str, Any]] = []
        while url and len(sites) < max_results:
            payload = await graph_get(url, headers)
            for site in payload.get("value", []):
                site_id = str(site.get("id") or "")
                site_entry: dict[str, Any] = {
                    "id": site_id,
                    "displayName": str(site.get("displayName") or ""),
                    "webUrl": str(site.get("webUrl") or ""),
                }

                if include_libraries and site_id:
                    try:
                        site_entry["libraries"] = await self._list_site_libraries(
                            site_id=site_id,
                            graph_get=graph_get,
                            headers=headers,
                        )
                    except Exception:
                        # Preserve site discovery response even if one site's library expansion fails.
                        site_entry["libraries"] = []

                sites.append(site_entry)
                if len(sites) >= max_results:
                    break
            url = payload.get("@odata.nextLink")

        return sites

    async def _list_site_libraries(
        self,
        *,
        site_id: str,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
    ) -> list[dict[str, str]]:
        encoded_site_id = urllib.parse.quote(site_id, safe="")
        url: str | None = f"{self._graph_base_url}/sites/{encoded_site_id}/drives?$select=id,name,driveType"
        libraries: list[dict[str, str]] = []

        while url:
            payload = await graph_get(url, headers)
            for drive in payload.get("value", []):
                if not isinstance(drive, dict):
                    continue

                drive_id = str(drive.get("id") or "").strip()
                if not drive_id:
                    continue

                drive_type = str(drive.get("driveType") or "").strip()
                if drive_type and drive_type != "documentLibrary":
                    continue

                libraries.append(
                    {
                        "drive_id": drive_id,
                        "library_name": str(drive.get("name") or "").strip(),
                    }
                )

            next_link = payload.get("@odata.nextLink")
            url = str(next_link).strip() if next_link else None

        return libraries

    @staticmethod
    def normalize_sites_search(search: str | None) -> str:
        """Normalize route input to Graph-compatible site search syntax."""
        value = (search or "").strip()
        if not value:
            return "*"
        if value == "*":
            return value
        if "*" in value:
            return value
        return f"{value}*"
