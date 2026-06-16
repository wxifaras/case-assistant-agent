"""SharePoint directory service.

Encapsulates Microsoft Graph calls for:

- :meth:`SharePointService.get_sites`        — enumerate visible sites.
- :meth:`SharePointService.get_site_members` — members of a site's connected
  Microsoft 365 group.
- :meth:`SharePointService.get_member_sites` — sites a given user belongs to
  (provided for completeness; not used by the broadcast flow).

Auth via :class:`DefaultAzureCredential`. Required Graph **application**
permissions, admin-consented: ``Sites.Read.All``, ``Group.Read.All``,
``User.Read.All``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

# Load .env to ensure DefaultAzureCredential uses EnvironmentCredential (service principal)
load_dotenv()

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data models                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SharePointSite:
    id: str
    name: str
    display_name: str
    hostname: str
    site_path: str
    web_url: str


@dataclass(frozen=True)
class SharePointMember:
    aad_object_id: str
    display_name: str
    email: str
    upn: str


# --------------------------------------------------------------------------- #
# Interface                                                                   #
# --------------------------------------------------------------------------- #


class ISharePointService(Protocol):
    """Public contract for the SharePoint directory service."""

    async def get_sites(
        self, *, search: str = "*", max_results: int = 200
    ) -> list[SharePointSite]: ...

    async def get_site_members(
        self, site: SharePointSite
    ) -> list[SharePointMember]: ...

    async def get_member_sites(
        self, *, user_id: str, max_results: int = 200
    ) -> list[SharePointSite]: ...

    async def close(self) -> None: ...


# --------------------------------------------------------------------------- #
# Implementation                                                              #
# --------------------------------------------------------------------------- #


class SharePointService:
    """Async wrapper over Microsoft Graph for site / member lookup.

    HTTP client and credential are created lazily and released via
    :meth:`close`. Safe to use as a process-wide singleton.
    """

    def __init__(self, *, request_timeout_seconds: float = 30.0) -> None:
        self._timeout = httpx.Timeout(request_timeout_seconds)
        self._http: httpx.AsyncClient | None = None
        self._credential: DefaultAzureCredential | None = None

    # ---- lifecycle ---- #

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._credential is not None:
            await self._credential.close()
            self._credential = None

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    def _ensure_credential(self) -> DefaultAzureCredential:
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential

    # ---- graph plumbing ---- #

    async def _headers(self) -> dict[str, str]:
        token = await self._ensure_credential().get_token(GRAPH_SCOPE)
        return {
            "Authorization": f"Bearer {token.token}",
            "ConsistencyLevel": "eventual",
        }

    async def _graph_get(
        self, url: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        response = await self._ensure_http().get(url, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Graph GET {url} failed with {response.status_code}: "
                f"{response.text[:500]}"
            )
        return response.json()

    # ---- public API ---- #

    async def get_sites(
        self, *, search: str = "*", max_results: int = 200
    ) -> list[SharePointSite]:
        headers = await self._headers()
        sites: list[SharePointSite] = []
        url = f"{GRAPH_BASE}/sites?search={quote(search)}&$top=100"

        while url and len(sites) < max_results:
            payload = await self._graph_get(url, headers)
            for raw in payload.get("value", []):
                parsed = self._parse_site(raw)
                if parsed:
                    sites.append(parsed)
                    if len(sites) >= max_results:
                        break
            url = payload.get("@odata.nextLink")

        logger.info("Listed %d SharePoint sites", len(sites))
        return sites

    async def get_site_members(
        self, site: SharePointSite
    ) -> list[SharePointMember]:
        headers = await self._headers()
        group_id = await self._resolve_connected_group_id(headers, site)
        if not group_id:
            logger.info("No connected M365 group for site %s", site.web_url)
            return []
        return await self._list_group_members(headers, group_id)

    async def get_member_sites(
        self, *, user_id: str, max_results: int = 200
    ) -> list[SharePointSite]:
        headers = await self._headers()
        url = (
            f"{GRAPH_BASE}/users/{user_id}/memberOf"
            "?$select=id,displayName,groupTypes&$top=100"
        )
        sites: list[SharePointSite] = []
        while url and len(sites) < max_results:
            payload = await self._graph_get(url, headers)
            for grp in payload.get("value", []):
                if "Unified" not in (grp.get("groupTypes") or []):
                    continue
                gid = grp.get("id")
                if not gid:
                    continue
                site = await self._site_for_group(headers, gid)
                if site:
                    sites.append(site)
                    if len(sites) >= max_results:
                        break
            url = payload.get("@odata.nextLink")
        return sites

    # ---- internals ---- #

    @staticmethod
    def _parse_site(raw: dict[str, Any]) -> SharePointSite | None:
        site_id = raw.get("id")
        if not site_id:
            return None
        web_url = raw.get("webUrl") or ""
        hostname = ""
        site_path = ""
        if web_url.startswith("https://"):
            tail = web_url[len("https://") :]
            slash = tail.find("/")
            if slash > 0:
                hostname = tail[:slash]
                site_path = tail[slash:]
            else:
                hostname = tail
        return SharePointSite(
            id=site_id,
            name=raw.get("name") or "",
            display_name=raw.get("displayName") or raw.get("name") or "",
            hostname=hostname,
            site_path=site_path,
            web_url=web_url,
        )

    async def _resolve_connected_group_id(
        self, headers: dict[str, str], site: SharePointSite
    ) -> str | None:
        """Find the M365 group tied to a SharePoint site.

        Strategy 1: ``mailNickname`` matches the last path segment (default
        for M365-connected team sites).
        Strategy 2: ``$search`` groups by site display name.
        """
        nickname = site.site_path.rstrip("/").rsplit("/", 1)[-1]
        if nickname:
            filt = quote(f"mailNickname eq '{nickname}'", safe="")
            url = f"{GRAPH_BASE}/groups?$filter={filt}&$select=id,displayName"
            payload = await self._graph_get(url, headers)
            for grp in payload.get("value", []):
                gid = grp.get("id")
                if gid:
                    return gid

        if not site.display_name:
            return None
        search = quote(f'"displayName:{site.display_name}"', safe='":')
        url = f"{GRAPH_BASE}/groups?$search={search}&$select=id,displayName"
        payload = await self._graph_get(url, headers)
        for grp in payload.get("value", []):
            if grp.get("displayName") == site.display_name:
                return grp["id"]
        return None

    async def _list_group_members(
        self, headers: dict[str, str], group_id: str
    ) -> list[SharePointMember]:
        members: list[SharePointMember] = []
        url = (
            f"{GRAPH_BASE}/groups/{group_id}/members"
            "?$select=id,displayName,mail,userPrincipalName&$top=100"
        )
        while url:
            payload = await self._graph_get(url, headers)
            for user in payload.get("value", []):
                odata_type = user.get("@odata.type")
                if odata_type and odata_type != "#microsoft.graph.user":
                    continue
                uid = user.get("id")
                if not uid:
                    continue
                members.append(
                    SharePointMember(
                        aad_object_id=uid,
                        display_name=user.get("displayName") or "",
                        email=user.get("mail") or "",
                        upn=user.get("userPrincipalName") or "",
                    )
                )
            url = payload.get("@odata.nextLink")
        return members

    async def _site_for_group(
        self, headers: dict[str, str], group_id: str
    ) -> SharePointSite | None:
        url = f"{GRAPH_BASE}/groups/{group_id}/sites/root"
        try:
            raw = await self._graph_get(url, headers)
        except RuntimeError:
            return None
        return self._parse_site(raw)
