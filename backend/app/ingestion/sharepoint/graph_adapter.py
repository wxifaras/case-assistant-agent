"""Protocol definitions for the SharePoint Graph adapter abstraction.

Two implementations are provided:
  - HttpxSharePointGraphAdapter  (httpx_graph_adapter.py) — current behaviour
  - MsgraphSharePointGraphAdapter (msgraph_adapter.py)    — msgraph-sdk

Select via ``SHAREPOINT_GRAPH_BACKEND=httpx|sdk`` (default: ``httpx``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from app.api.schemas.sharepoint import SharePointSyncRequest


@runtime_checkable
class SharePointGraphAdapter(Protocol):
    """All high-level Microsoft Graph operations required by SharePoint sync."""

    async def resolve_site_info(self, hostname: str, site_path: str) -> tuple[str, str]:
        """Return ``(site_id, display_name)`` for the given site."""
        ...

    async def resolve_drive_id(
        self,
        request: SharePointSyncRequest,
        site_id: str,
        library_name: str | None,
    ) -> str:
        """Return the drive (document library) ID."""
        ...

    async def resolve_folder_item_id(self, drive_id: str, folder_path: str | None) -> str:
        """Return the item ID of the root folder (or drive root when *folder_path* is ``None``)."""
        ...

    def iter_files(
        self,
        drive_id: str,
        folder_item_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield file metadata dicts recursively from *folder_item_id*."""
        ...

    async def get_sites(
        self,
        search: str,
        max_results: int,
        include_libraries: bool,
    ) -> list[dict[str, Any]]:
        """Return a list of SharePoint site metadata dicts."""
        ...

    async def get_site_members(
        self,
        site_hostname: str,
        site_path: str,
        tenant_id: str | None,
    ) -> list[dict[str, str]]:
        """Return a list of site member dicts."""
        ...

    async def get_member_sites(
        self,
        user_id: str,
        search: str,
        max_results: int,
        tenant_id: str | None,
    ) -> list[dict[str, str]]:
        """Return a list of site dicts the given user is a member of."""
        ...

    async def download_file_content(self, drive_id: str, item_id: str) -> bytes:
        """Download raw file bytes from a drive item (fallback — prefer downloadUrl)."""
        ...


@runtime_checkable
class IGraphAdapterFactory(Protocol):
    """Creates per-request ``SharePointGraphAdapter`` instances."""

    async def create_async(self, delegated_token: str | None) -> SharePointGraphAdapter:
        """Return an adapter that uses *delegated_token* (or app identity when ``None``)."""
        ...

    async def close(self) -> None:
        """Release any shared resources (HTTP client, credentials)."""
        ...
