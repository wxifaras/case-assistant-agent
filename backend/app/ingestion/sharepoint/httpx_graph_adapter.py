"""httpx-based implementation of ``SharePointGraphAdapter``.

This is a zero-behaviour-change relocation of the logic that previously
lived across ``graph_client.py``, ``membership_service.py``,
``site_discovery_service.py``, and the ``/content`` path of
``transfer_service.py``.

Select this backend by setting ``SHAREPOINT_GRAPH_BACKEND=httpx``
(the default).
"""

from __future__ import annotations

import urllib.parse
from collections.abc import AsyncIterator
from typing import Any

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.api.schemas.sharepoint import SharePointSyncRequest
from app.core.settings import Settings
from app.ingestion.sharepoint.graph_adapter import SharePointGraphAdapter
from app.models.sharepoint import SharePointSiteMemberItem


class HttpxSharePointGraphAdapter:
    """Graph adapter backed by raw ``httpx`` async calls."""

    def __init__(
        self,
        *,
        graph_base_url: str,
        token: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._base = graph_base_url
        self._token = token
        self._http = http_client

    # ------------------------------------------------------------------ #
    # internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _auth_headers(self, *, consistency: bool = False) -> dict[str, str]:
        h: dict[str, str] = {"Authorization": f"Bearer {self._token}"}
        if consistency:
            h["ConsistencyLevel"] = "eventual"
        return h

    async def _get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        response = await self._http.get(url, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Graph GET {url} failed with {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    # ------------------------------------------------------------------ #
    # SharePointGraphAdapter protocol                                    #
    # ------------------------------------------------------------------ #

    async def resolve_site_info(self, hostname: str, site_path: str) -> tuple[str, str]:
        path = site_path if site_path.startswith("/") else f"/{site_path}"
        data = await self._get(
            f"{self._base}/sites/{hostname}:{path}",
            self._auth_headers(),
        )
        site_id = data.get("id")
        if not site_id:
            raise ValueError(f"Could not resolve SharePoint site at {hostname}{path}.")
        display_name = str(data.get("displayName") or data.get("name") or "").strip()
        return site_id, display_name

    async def resolve_drive_id(
        self,
        request: SharePointSyncRequest,
        site_id: str,
        library_name: str | None,
    ) -> str:
        if request.drive_id:
            return request.drive_id
        if not library_name:
            raise ValueError("Either drive_id or library_name must be provided.")
        data = await self._get(
            f"{self._base}/sites/{site_id}/drives",
            self._auth_headers(),
        )
        for drive in data.get("value", []):
            if drive.get("name") == library_name:
                drive_id = drive.get("id")
                if drive_id:
                    return drive_id
        raise ValueError(f"Library '{library_name}' not found on resolved site '{site_id}'.")

    async def resolve_folder_item_id(self, drive_id: str, folder_path: str | None) -> str:
        if not folder_path:
            url = f"{self._base}/drives/{drive_id}/root"
        else:
            encoded = urllib.parse.quote(folder_path.strip("/"))
            url = f"{self._base}/drives/{drive_id}/root:/{encoded}"
        data = await self._get(url, self._auth_headers())
        item_id = data.get("id")
        if not item_id:
            raise ValueError(f"Folder '{folder_path or '/'}' not found in drive {drive_id}.")
        return item_id

    async def iter_files(
        self,
        drive_id: str,
        folder_item_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        headers = self._auth_headers()
        stack: list[tuple[str, str]] = [(folder_item_id, "")]
        while stack:
            current_id, rel_prefix = stack.pop()
            url: str | None = (
                f"{self._base}/drives/{drive_id}/items/{current_id}/children"
                "?$select=id,name,size,eTag,lastModifiedDateTime,folder,file,"
                "parentReference,@microsoft.graph.downloadUrl"
            )
            while url:
                data = await self._get(url, headers)
                for entry in data.get("value", []):
                    name = entry.get("name", "")
                    rel_path = f"{rel_prefix}{name}"
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

    async def get_sites(
        self,
        search: str,
        max_results: int,
        include_libraries: bool,
    ) -> list[dict[str, Any]]:
        normalized = _normalize_sites_search(search)
        encoded = urllib.parse.quote(normalized)
        url: str | None = f"{self._base}/sites?search={encoded}&$select=id,displayName,webUrl"
        headers = self._auth_headers(consistency=True)

        sites: list[dict[str, Any]] = []
        while url and len(sites) < max_results:
            payload = await self._get(url, headers)
            for site in payload.get("value", []):
                site_id = str(site.get("id") or "")
                entry: dict[str, Any] = {
                    "id": site_id,
                    "displayName": str(site.get("displayName") or ""),
                    "webUrl": str(site.get("webUrl") or ""),
                }
                if include_libraries and site_id:
                    try:
                        entry["libraries"] = await self._list_site_libraries(site_id)
                    except Exception:
                        entry["libraries"] = []
                sites.append(entry)
                if len(sites) >= max_results:
                    break
            url = payload.get("@odata.nextLink")
        return sites

    async def _list_site_libraries(self, site_id: str) -> list[dict[str, str]]:
        encoded_id = urllib.parse.quote(site_id, safe="")
        url: str | None = f"{self._base}/sites/{encoded_id}/drives?$select=id,name,driveType"
        headers = self._auth_headers()
        libraries: list[dict[str, str]] = []
        while url:
            payload = await self._get(url, headers)
            for drive in payload.get("value", []):
                if not isinstance(drive, dict):
                    continue
                drive_id = str(drive.get("id") or "").strip()
                if not drive_id:
                    continue
                drive_type = str(drive.get("driveType") or "").strip()
                if drive_type and drive_type != "documentLibrary":
                    continue
                libraries.append({"drive_id": drive_id, "library_name": str(drive.get("name") or "").strip()})
            next_link = payload.get("@odata.nextLink")
            url = str(next_link).strip() if next_link else None
        return libraries

    async def get_site_members(
        self,
        site_hostname: str,
        site_path: str,
        tenant_id: str | None,
    ) -> list[dict[str, str]]:
        hostname = (site_hostname or "").strip()
        path = (site_path or "").strip()
        if not hostname or not path:
            raise ValueError("site_hostname and site_path are required.")

        resolved_tenant_id = (tenant_id or "").strip()
        headers = self._auth_headers(consistency=True)

        site_id, _ = await self.resolve_site_info(hostname, path)
        members = await self._fetch_site_members_from_connected_group(
            headers=headers,
            tenant_id=resolved_tenant_id,
            site_id=site_id,
            existing_members={},
        )
        members.sort(key=lambda m: (m.display_name or "", m.member_id or ""))
        return [
            {
                "member_id": m.member_id,
                "display_name": m.display_name,
                "email": m.email,
                "role": m.role,
                "source": m.source,
            }
            for m in members
        ]

    async def get_member_sites(
        self,
        user_id: str,
        search: str,
        max_results: int,
        tenant_id: str | None,
    ) -> list[dict[str, str]]:
        identifier = (user_id or "").strip()
        if not identifier:
            raise ValueError("user_id is required.")

        headers = self._auth_headers(consistency=True)
        sites = await self.get_sites(search=search, max_results=max_results, include_libraries=False)
        member_sites: list[dict[str, str]] = []

        for site in sites:
            site_id = str(site.get("id") or "").strip()
            if not site_id:
                continue
            group_id = await self._resolve_connected_group_id(headers=headers, site_id=site_id)
            if not group_id:
                continue
            if not await self._is_user_member_of_group(headers=headers, group_id=group_id, user_id=identifier):
                continue
            member_sites.append(
                {
                    "site_id": site_id,
                    "site_name": str(site.get("displayName") or ""),
                    "web_url": str(site.get("webUrl") or ""),
                }
            )

        member_sites.sort(key=lambda s: (s.get("site_name") or "", s.get("site_id") or ""))
        return member_sites

    async def download_file_content(self, drive_id: str, item_id: str) -> bytes:
        url = f"{self._base}/drives/{drive_id}/items/{item_id}/content"
        response = await self._http.get(url, headers=self._auth_headers(), follow_redirects=True)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Graph GET {url} failed with {response.status_code}: {response.text[:200]}"
            )
        return response.content

    # ------------------------------------------------------------------ #
    # membership helpers                                                 #
    # ------------------------------------------------------------------ #

    async def _resolve_connected_group_id(
        self,
        *,
        headers: dict[str, str],
        site_id: str,
    ) -> str | None:
        site = await self._get(f"{self._base}/sites/{site_id}?$select=displayName", headers)
        site_display_name = str(site.get("displayName") or "").strip()
        if not site_display_name:
            return None

        search_query = urllib.parse.quote(f'"displayName:{site_display_name}"')
        groups = await self._get(
            f"{self._base}/groups?$search={search_query}&$select=id,displayName",
            headers,
        )
        group_values = groups.get("value") or []
        if not group_values:
            return None

        exact = next(
            (g for g in group_values if str(g.get("displayName") or "").strip().lower() == site_display_name.lower()),
            None,
        )
        target = exact or group_values[0]
        return str(target.get("id") or "") or None

    async def _load_group_owner_ids(self, *, headers: dict[str, str], group_id: str) -> set[str]:
        url: str | None = f"{self._base}/groups/{group_id}/owners?$select=id"
        owner_ids: set[str] = set()
        while url:
            payload = await self._get(url, headers)
            for owner in payload.get("value", []):
                owner_id = str(owner.get("id") or "").strip()
                if owner_id:
                    owner_ids.add(owner_id)
            url = payload.get("@odata.nextLink")
        return owner_ids

    async def _is_user_member_of_group(
        self, *, headers: dict[str, str], group_id: str, user_id: str
    ) -> bool:
        identifier = str(user_id or "").strip().lower()
        if not identifier:
            return False
        url: str | None = (
            f"{self._base}/groups/{group_id}/transitiveMembers/microsoft.graph.user"
            "?$select=id,userPrincipalName,mail"
        )
        while url:
            payload = await self._get(url, headers)
            for member in payload.get("value", []):
                if identifier in {
                    str(member.get("id") or "").strip().lower(),
                    str(member.get("userPrincipalName") or "").strip().lower(),
                    str(member.get("mail") or "").strip().lower(),
                }:
                    return True
            url = payload.get("@odata.nextLink")
        return False

    async def _fetch_site_members_from_connected_group(
        self,
        *,
        headers: dict[str, str],
        tenant_id: str,
        site_id: str,
        existing_members: dict[str, SharePointSiteMemberItem],
    ) -> list[SharePointSiteMemberItem]:
        import uuid

        group_id = await self._resolve_connected_group_id(headers=headers, site_id=site_id)
        if not group_id:
            return []

        owner_ids = await self._load_group_owner_ids(headers=headers, group_id=group_id)
        members: list[SharePointSiteMemberItem] = []

        url: str | None = (
            f"{self._base}/groups/{group_id}/transitiveMembers"
            "?$select=id,displayName,userPrincipalName,mail"
        )
        while url:
            payload = await self._get(url, headers)
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
                existing = existing_members.get(member_id)
                members.append(
                    SharePointSiteMemberItem(
                        id=existing.id if existing is not None else str(uuid.uuid4()),
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


class HttpxGraphAdapterFactory:
    """Factory that produces ``HttpxSharePointGraphAdapter`` instances."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._credential: DefaultAzureCredential | None = None
        self._http: httpx.AsyncClient | None = None

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            timeout = httpx.Timeout(self._settings.sharepoint.request_timeout_seconds)
            self._http = httpx.AsyncClient(timeout=timeout)
        return self._http

    def _ensure_credential(self) -> DefaultAzureCredential:
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential


    async def create_async(self, delegated_token: str | None) -> SharePointGraphAdapter:
        if delegated_token:
            token = delegated_token.strip()
        else:
            credential = self._ensure_credential()
            t = await credential.get_token(self._settings.sharepoint.graph_scope)
            token = t.token
        return HttpxSharePointGraphAdapter(
            graph_base_url=self._settings.sharepoint.graph_base_url,
            token=token,
            http_client=self._ensure_http(),
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._credential is not None:
            await self._credential.close()
            self._credential = None


# ---------------------------------------------------------------------------
# utility shared by both adapters
# ---------------------------------------------------------------------------


def _normalize_sites_search(search: str | None) -> str:
    value = (search or "").strip()
    if not value or value == "*":
        return value or "*"
    if "*" in value:
        return value
    return f"{value}*"
