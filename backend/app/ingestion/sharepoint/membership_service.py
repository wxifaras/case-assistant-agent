from __future__ import annotations

import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any

from app.models.sharepoint import SharePointSiteMemberItem


class SharePointMembershipService:
    """Membership-oriented Graph operations used by SharePoint sync service."""

    def __init__(
        self,
        *,
        graph_base_url: str,
    ) -> None:
        self._graph_base_url = graph_base_url

    async def get_member_sites(
        self,
        *,
        user_id: str,
        search: str,
        max_results: int,
        tenant_id: str | None,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        acquire_graph_token: Callable[[], Awaitable[str]],
        resolve_tenant_id: Callable[[str | None], str],
        get_sites: Callable[..., Awaitable[list[dict[str, str]]]],
        resolve_connected_group_id: Callable[..., Awaitable[str | None]],
        is_user_member_of_group: Callable[..., Awaitable[bool]],
    ) -> list[dict[str, str]]:
        identifier = (user_id or "").strip()
        if not identifier:
            raise ValueError("user_id is required.")

        _ = resolve_tenant_id(tenant_id)
        token = await acquire_graph_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
        }

        sites = await get_sites(search=search, max_results=max_results)
        member_sites: list[dict[str, str]] = []
        for site in sites:
            site_id = str(site.get("id") or "").strip()
            if not site_id:
                continue

            group_id = await resolve_connected_group_id(headers=headers, site_id=site_id)
            if not group_id:
                continue

            is_member = await is_user_member_of_group(
                headers=headers,
                group_id=group_id,
                user_id=identifier,
            )
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
        tenant_id: str | None,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        acquire_graph_token: Callable[[], Awaitable[str]],
        resolve_tenant_id: Callable[[str | None], str],
        resolve_site_info: Callable[..., Awaitable[tuple[str, str]]],
        fetch_site_members_from_connected_group: Callable[..., Awaitable[list[SharePointSiteMemberItem]]],
    ) -> list[dict[str, str]]:
        hostname = (site_hostname or "").strip()
        path = (site_path or "").strip()
        if not hostname or not path:
            raise ValueError("site_hostname and site_path are required.")

        resolved_tenant_id = resolve_tenant_id(tenant_id)
        token = await acquire_graph_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
        }

        site_id, _ = await resolve_site_info(headers=headers, hostname=hostname, site_path=path)
        members = await fetch_site_members_from_connected_group(
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

    async def fetch_site_members_from_connected_group(
        self,
        *,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
        tenant_id: str,
        site_id: str,
        existing_members: dict[str, SharePointSiteMemberItem],
        resolve_connected_group_id: Callable[..., Awaitable[str | None]],
        load_group_owner_ids: Callable[..., Awaitable[set[str]]],
        build_member_id: Callable[[str | None], str],
    ) -> list[SharePointSiteMemberItem]:
        group_id = await resolve_connected_group_id(headers=headers, site_id=site_id)
        if not group_id:
            return []

        owner_ids = await load_group_owner_ids(headers=headers, group_id=group_id)
        members: list[SharePointSiteMemberItem] = []

        url: str | None = (
            f"{self._graph_base_url}/groups/{group_id}/transitiveMembers"
            "?$select=id,displayName,userPrincipalName,mail"
        )
        while url:
            payload = await graph_get(url, headers)
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
                        id=build_member_id(existing_member.id if existing_member is not None else None),
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

    async def resolve_connected_group_id(
        self,
        *,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
        site_id: str,
    ) -> str | None:
        site = await graph_get(f"{self._graph_base_url}/sites/{site_id}?$select=displayName", headers)
        site_display_name = str(site.get("displayName") or "").strip()
        if not site_display_name:
            return None

        search_query = urllib.parse.quote(f'"displayName:{site_display_name}"')
        groups = await graph_get(
            f"{self._graph_base_url}/groups?$search={search_query}&$select=id,displayName", headers
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

    async def load_group_owner_ids(
        self,
        *,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
        group_id: str,
    ) -> set[str]:
        url: str | None = f"{self._graph_base_url}/groups/{group_id}/owners?$select=id"
        owner_ids: set[str] = set()

        while url:
            payload = await graph_get(url, headers)
            for owner in payload.get("value", []):
                owner_id = str(owner.get("id") or "").strip()
                if owner_id:
                    owner_ids.add(owner_id)
            url = payload.get("@odata.nextLink")

        return owner_ids

    async def is_user_member_of_group(
        self,
        *,
        graph_get: Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]],
        headers: dict[str, str],
        group_id: str,
        user_id: str,
    ) -> bool:
        identifier = str(user_id or "").strip().lower()
        if not identifier:
            return False

        url: str | None = (
            f"{self._graph_base_url}/groups/{group_id}/transitiveMembers/microsoft.graph.user"
            "?$select=id,userPrincipalName,mail"
        )
        while url:
            payload = await graph_get(url, headers)
            for member in payload.get("value", []):
                member_id = str(member.get("id") or "").strip().lower()
                member_upn = str(member.get("userPrincipalName") or "").strip().lower()
                member_mail = str(member.get("mail") or "").strip().lower()
                if identifier in {member_id, member_upn, member_mail}:
                    return True
            url = payload.get("@odata.nextLink")

        return False
