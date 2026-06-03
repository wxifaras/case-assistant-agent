"""msgraph-sdk-based implementation of ``SharePointGraphAdapter``.

Requires ``msgraph-sdk>=1.21.0`` (already in ``pyproject.toml``).

Select this backend by setting ``SHAREPOINT_GRAPH_BACKEND=sdk``.
"""

from __future__ import annotations

import urllib.parse
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from msgraph import GraphServiceClient

from app.api.schemas.sharepoint import SharePointSyncRequest
from app.core.settings import Settings
from app.ingestion.sharepoint.graph_adapter import SharePointGraphAdapter
from app.models.sharepoint import SharePointSiteMemberItem

# ---------------------------------------------------------------------------
# Credential adapters
# ---------------------------------------------------------------------------


class _StaticTokenCredential(AsyncTokenCredential):
    """Wraps a pre-resolved bearer token as an ``AsyncTokenCredential``."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: Any,
    ) -> AccessToken:
        import time

        return AccessToken(self._token, int(time.time()) + 3600)

    async def close(self) -> None:  # noqa: D401
        pass

    async def __aenter__(self) -> _StaticTokenCredential:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class MsgraphSharePointGraphAdapter:
    """Graph adapter backed by the official ``msgraph-sdk``."""

    def __init__(
        self,
        *,
        graph_base_url: str,
        credential: AsyncTokenCredential,
        scopes: list[str],
    ) -> None:
        self._base = graph_base_url
        self._client = GraphServiceClient(credential, scopes=scopes)

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ensure(value: Any, label: str) -> str:
        s = str(value or "").strip()
        if not s:
            raise ValueError(f"Expected a non-empty value for '{label}', got: {value!r}")
        return s

    # ------------------------------------------------------------------ #
    # SharePointGraphAdapter protocol                                    #
    # ------------------------------------------------------------------ #

    async def resolve_site_info(self, hostname: str, site_path: str) -> tuple[str, str]:
        path = site_path if site_path.startswith("/") else f"/{site_path}"
        site = await self._client.sites.by_site_id(f"{hostname}:{path}").get()
        if site is None or not site.id:
            raise ValueError(f"Could not resolve SharePoint site at {hostname}{path}.")
        display_name = str(site.display_name or site.name or "").strip()
        return site.id, display_name

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
        response = await self._client.sites.by_site_id(site_id).drives.get()
        for drive in (response.value or []) if response else []:
            if drive.name == library_name and drive.id:
                return drive.id
        raise ValueError(f"Library '{library_name}' not found on resolved site '{site_id}'.")

    async def resolve_folder_item_id(self, drive_id: str, folder_path: str | None) -> str:
        if not folder_path:
            item = await self._client.drives.by_drive_id(drive_id).root.get()
        else:
            encoded = urllib.parse.quote(folder_path.strip("/"))
            root_builder = self._client.drives.by_drive_id(drive_id).root
            # Older stubs may not expose item_with_path even when runtime does.
            item_with_path = cast(Any, root_builder).item_with_path
            item = await item_with_path(encoded).get()
        if item is None or not item.id:
            raise ValueError(f"Folder '{folder_path or '/'}' not found in drive {drive_id}.")
        return item.id

    async def iter_files(
        self,
        drive_id: str,
        folder_item_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        stack: list[tuple[str, str]] = [(folder_item_id, "")]
        while stack:
            current_id, rel_prefix = stack.pop()
            page = await (
                self._client.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(current_id)
                .children.get(
                    request_configuration=_select_fields_config(
                        "id,name,size,eTag,lastModifiedDateTime,folder,file,parentReference"
                    )
                )
            )
            while page is not None:
                for item in page.value or []:
                    name = item.name or ""
                    rel_path = f"{rel_prefix}{name}"
                    parent_ref = item.parent_reference
                    parent_path = (parent_ref.path if parent_ref else None) or ""
                    source_path = f"{parent_path}/{name}" if parent_path else name

                    if item.folder is not None:
                        if item.id:
                            stack.append((item.id, f"{rel_path}/"))
                        continue
                    if item.file is None:
                        continue

                    entry: dict[str, Any] = {
                        "id": item.id,
                        "name": name,
                        "size": item.size,
                        "eTag": item.e_tag,
                        "lastModifiedDateTime": (
                            item.last_modified_date_time.isoformat() if item.last_modified_date_time else None
                        ),
                        "_source_path": source_path,
                        "_relative_path": rel_path,
                        # SDK does not return downloadUrl in the listing by default;
                        # callers should fall back to copy_drive_item_to_blob.
                        "@microsoft.graph.downloadUrl": None,
                    }
                    yield entry

                # Pagination
                if page.odata_next_link:
                    from msgraph.generated.drives.item.items.item.children.children_request_builder import (
                        ChildrenRequestBuilder,
                    )

                    page = await ChildrenRequestBuilder(self._client.request_adapter, page.odata_next_link).get()
                else:
                    break

    async def get_sites(
        self,
        search: str,
        max_results: int,
        include_libraries: bool,
    ) -> list[dict[str, Any]]:
        from msgraph.generated.sites.sites_request_builder import SitesRequestBuilder

        normalized = _normalize_sites_search(search)
        query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters(
            search=normalized,
            select=["id", "displayName", "webUrl"],
        )
        config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration(
            query_parameters=query_params,
            headers=cast(Any, {"ConsistencyLevel": "eventual"}),
        )

        sites: list[dict[str, Any]] = []
        response = await self._client.sites.get(request_configuration=config)
        while response is not None and len(sites) < max_results:
            for site in response.value or []:
                site_id = str(site.id or "")
                entry: dict[str, Any] = {
                    "id": site_id,
                    "displayName": str(site.display_name or ""),
                    "webUrl": str(site.web_url or ""),
                }
                if include_libraries and site_id:
                    try:
                        entry["libraries"] = await self._list_site_libraries(site_id)
                    except Exception:
                        entry["libraries"] = []
                sites.append(entry)
                if len(sites) >= max_results:
                    break
            if response.odata_next_link:
                from msgraph.generated.sites.sites_request_builder import SitesRequestBuilder as _SR

                response = await _SR(self._client.request_adapter, response.odata_next_link).get()
            else:
                break
        return sites

    async def _list_site_libraries(self, site_id: str) -> list[dict[str, str]]:
        from msgraph.generated.sites.item.drives.drives_request_builder import DrivesRequestBuilder

        config = DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration(
            query_parameters=DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters(
                select=["id", "name", "driveType"]
            )
        )
        response = await self._client.sites.by_site_id(site_id).drives.get(request_configuration=config)
        libraries: list[dict[str, str]] = []
        while response is not None:
            for drive in response.value or []:
                drive_id = str(drive.id or "").strip()
                if not drive_id:
                    continue
                drive_type = str(drive.drive_type or "").strip()
                if drive_type and drive_type != "documentLibrary":
                    continue
                libraries.append({"drive_id": drive_id, "library_name": str(drive.name or "").strip()})
            if response.odata_next_link:
                from msgraph.generated.sites.item.drives.drives_request_builder import (
                    DrivesRequestBuilder as _DR,
                )

                response = await _DR(self._client.request_adapter, response.odata_next_link).get()
            else:
                break
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
        site_id, _ = await self.resolve_site_info(hostname, path)
        members = await self._fetch_site_members_from_connected_group(
            tenant_id=resolved_tenant_id,
            site_id=site_id,
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
        sites = await self.get_sites(search=search, max_results=max_results, include_libraries=False)
        member_sites: list[dict[str, str]] = []
        for site in sites:
            site_id = str(site.get("id") or "").strip()
            if not site_id:
                continue
            group_id = await self._resolve_connected_group_id(site_id=site_id)
            if not group_id:
                continue
            if not await self._is_user_member_of_group(group_id=group_id, user_id=identifier):
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
        stream = await self._client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).content.get()
        if stream is None:
            raise RuntimeError(f"No content returned for drive={drive_id} item={item_id}.")
        if isinstance(stream, bytes):
            return stream
        # Some SDK versions return an IO stream
        return stream.read()  # type: ignore[union-attr]

    # ------------------------------------------------------------------ #
    # membership helpers                                                 #
    # ------------------------------------------------------------------ #

    async def _resolve_connected_group_id(self, *, site_id: str) -> str | None:
        site = await self._client.sites.by_site_id(site_id).get()
        display_name = str(site.display_name if site else "" or "").strip()
        if not display_name:
            return None

        from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder

        search_value = f'"displayName:{display_name}"'
        config_g = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration(
            query_parameters=GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
                search=search_value,
                select=["id", "displayName"],
            ),
            headers=cast(Any, {"ConsistencyLevel": "eventual"}),
        )
        response = await self._client.groups.get(request_configuration=config_g)
        group_values = (response.value or []) if response else []
        if not group_values:
            return None

        exact = next(
            (g for g in group_values if str(g.display_name or "").strip().lower() == display_name.lower()),
            None,
        )
        target = exact or group_values[0]
        return str(target.id or "") or None

    async def _load_group_owner_ids(self, *, group_id: str) -> set[str]:
        from msgraph.generated.groups.item.owners.owners_request_builder import OwnersRequestBuilder

        config = OwnersRequestBuilder.OwnersRequestBuilderGetRequestConfiguration(
            query_parameters=OwnersRequestBuilder.OwnersRequestBuilderGetQueryParameters(select=["id"])
        )
        owner_ids: set[str] = set()
        response = await self._client.groups.by_group_id(group_id).owners.get(request_configuration=config)
        while response is not None:
            for owner in response.value or []:
                oid = str(owner.id or "").strip()
                if oid:
                    owner_ids.add(oid)
            if response.odata_next_link:
                from msgraph.generated.groups.item.owners.owners_request_builder import (
                    OwnersRequestBuilder as _OR,
                )

                response = await _OR(self._client.request_adapter, response.odata_next_link).get()
            else:
                break
        return owner_ids

    async def _is_user_member_of_group(self, *, group_id: str, user_id: str) -> bool:
        identifier = user_id.strip().lower()
        if not identifier:
            return False

        from msgraph.generated.groups.item.transitive_members.transitive_members_request_builder import (
            TransitiveMembersRequestBuilder,
        )

        config = TransitiveMembersRequestBuilder.TransitiveMembersRequestBuilderGetRequestConfiguration(
            query_parameters=TransitiveMembersRequestBuilder.TransitiveMembersRequestBuilderGetQueryParameters(
                select=["id", "userPrincipalName", "mail"],
                filter="isof('microsoft.graph.user')",
            )
        )
        response = await self._client.groups.by_group_id(group_id).transitive_members.get(request_configuration=config)
        while response is not None:
            for member in response.value or []:
                if identifier in {
                    str(member.id or "").strip().lower(),
                    str(getattr(member, "user_principal_name", "") or "").strip().lower(),
                    str(getattr(member, "mail", "") or "").strip().lower(),
                }:
                    return True
            if response.odata_next_link:
                from msgraph.generated.groups.item.transitive_members.transitive_members_request_builder import (
                    TransitiveMembersRequestBuilder as _TM,
                )

                response = await _TM(self._client.request_adapter, response.odata_next_link).get()
            else:
                break
        return False

    async def _fetch_site_members_from_connected_group(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> list[SharePointSiteMemberItem]:
        group_id = await self._resolve_connected_group_id(site_id=site_id)
        if not group_id:
            return []

        owner_ids = await self._load_group_owner_ids(group_id=group_id)
        members: list[SharePointSiteMemberItem] = []

        from msgraph.generated.groups.item.transitive_members.transitive_members_request_builder import (
            TransitiveMembersRequestBuilder,
        )

        config = TransitiveMembersRequestBuilder.TransitiveMembersRequestBuilderGetRequestConfiguration(
            query_parameters=TransitiveMembersRequestBuilder.TransitiveMembersRequestBuilderGetQueryParameters(
                select=["id", "displayName", "userPrincipalName", "mail"]
            )
        )
        response = await self._client.groups.by_group_id(group_id).transitive_members.get(request_configuration=config)
        while response is not None:
            for principal in response.value or []:
                member_id = str(principal.id or "").strip()
                if not member_id:
                    continue
                display_name = str(
                    getattr(principal, "display_name", None)
                    or getattr(principal, "user_principal_name", None)
                    or getattr(principal, "mail", None)
                    or member_id
                )
                email = str(getattr(principal, "mail", None) or getattr(principal, "user_principal_name", None) or "")
                odata_type = str(getattr(principal, "odata_type", None) or "").split(".")[-1] or "principal"
                role = "owner" if member_id in owner_ids else "member"
                members.append(
                    SharePointSiteMemberItem(
                        id=str(uuid.uuid4()),
                        tenant_id=tenant_id,
                        site_id=site_id,
                        member_id=member_id,
                        display_name=display_name,
                        email=email,
                        role=role,
                        source=f"graph-group-transitive-{odata_type}",
                    )
                )
            if response.odata_next_link:
                from msgraph.generated.groups.item.transitive_members.transitive_members_request_builder import (
                    TransitiveMembersRequestBuilder as _TM,
                )

                response = await _TM(self._client.request_adapter, response.odata_next_link).get()
            else:
                break
        return members


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class MsgraphGraphAdapterFactory:
    """Factory that produces ``MsgraphSharePointGraphAdapter`` instances."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._credential: DefaultAzureCredential | None = None

    def _ensure_credential(self) -> DefaultAzureCredential:
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential

    async def create_async(self, delegated_token: str | None) -> SharePointGraphAdapter:
        if delegated_token:
            credential: AsyncTokenCredential = _StaticTokenCredential(delegated_token.strip())
            scopes = [self._settings.sharepoint.graph_scope]
        else:
            credential = self._ensure_credential()
            scopes = [self._settings.sharepoint.graph_scope]
        return MsgraphSharePointGraphAdapter(
            graph_base_url=self._settings.sharepoint.graph_base_url,
            credential=credential,
            scopes=scopes,
        )

    async def close(self) -> None:
        if self._credential is not None:
            await self._credential.close()
            self._credential = None


# ---------------------------------------------------------------------------
# shared utilities
# ---------------------------------------------------------------------------


def _normalize_sites_search(search: str | None) -> str:
    value = (search or "").strip()
    if not value or value == "*":
        return value or "*"
    if "*" in value:
        return value
    return f"{value}*"


def _select_fields_config(fields: str) -> Any:
    """Return a request configuration that sets ``$select`` on children queries."""
    try:
        from msgraph.generated.drives.item.items.item.children.children_request_builder import (
            ChildrenRequestBuilder,
        )

        return ChildrenRequestBuilder.ChildrenRequestBuilderGetRequestConfiguration(
            query_parameters=ChildrenRequestBuilder.ChildrenRequestBuilderGetQueryParameters(select=fields.split(","))
        )
    except Exception:
        return None
