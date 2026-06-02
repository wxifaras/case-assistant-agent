import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes import sharepoint
from app.api.schemas.sharepoint import SharePointSyncRequest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_sites_returns_success_payload() -> None:
    service = MagicMock()
    service.get_sites = AsyncMock(
        return_value=[
            {
                "id": "site-1",
                "displayName": "Site One",
                "webUrl": "https://contoso.sharepoint.com/sites/site-one",
            }
        ]
    )

    response = await sharepoint.get_sites(
        search="*",
        max_results=50,
        sharepoint_service=service,
        logger=MagicMock(),
    )

    assert response.status_code == 200
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["message"] == "SharePoint sites retrieved"
    assert payload["data"]["count"] == 1
    assert payload["data"]["sites"][0]["id"] == "site-1"
    service.get_sites.assert_awaited_once_with(search="*", max_results=50, include_libraries=False)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_sites_returns_400_for_invalid_request() -> None:
    service = MagicMock()
    service.get_sites = AsyncMock(side_effect=ValueError("bad search"))

    response = await sharepoint.get_sites(
        search="*",
        max_results=50,
        sharepoint_service=service,
        logger=MagicMock(),
    )

    assert response.status_code == 400
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["error"] == "Invalid list sites request"
    assert payload["details"] == "bad search"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_member_sites_returns_success_payload() -> None:
    service = MagicMock()
    service.get_member_sites = AsyncMock(
        return_value=[
            {
                "site_id": "site-1",
                "site_name": "Site One",
                "web_url": "https://contoso.sharepoint.com/sites/site-one",
            }
        ]
    )

    response = await sharepoint.get_member_sites(
        user_id="member-1",
        search="*",
        max_results=50,
        tenant_id="tenant-1",
        sharepoint_service=service,
        logger=MagicMock(),
    )

    assert response.status_code == 200
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["message"] == "SharePoint member sites retrieved"
    assert payload["data"]["count"] == 1
    assert payload["data"]["sites"][0]["site_id"] == "site-1"
    service.get_member_sites.assert_awaited_once_with(
        user_id="member-1",
        search="*",
        max_results=50,
        tenant_id="tenant-1",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_member_sites_returns_400_for_invalid_request() -> None:
    service = MagicMock()
    service.get_member_sites = AsyncMock(side_effect=ValueError("missing user"))

    response = await sharepoint.get_member_sites(
        user_id="member-1",
        search="*",
        max_results=50,
        tenant_id="tenant-1",
        sharepoint_service=service,
        logger=MagicMock(),
    )

    assert response.status_code == 400
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["error"] == "Invalid member-of request"
    assert payload["details"] == "missing user"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_site_members_graph_returns_success_payload() -> None:
    service = MagicMock()
    service.get_site_members = AsyncMock(
        return_value=[
            {
                "member_id": "member-1",
                "display_name": "Member One",
                "email": "member.one@contoso.com",
                "role": "owner",
                "source": "graph-group-transitive-user",
            }
        ]
    )

    response = await sharepoint.get_site_members(
        site_hostname="contoso.sharepoint.com",
        site_path="/sites/site-one",
        tenant_id="tenant-1",
        sharepoint_service=service,
        logger=MagicMock(),
    )

    assert response.status_code == 200
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["message"] == "SharePoint site members retrieved"
    assert payload["data"]["count"] == 1
    assert payload["data"]["members"][0]["member_id"] == "member-1"
    service.get_site_members.assert_awaited_once_with(
        site_hostname="contoso.sharepoint.com",
        site_path="/sites/site-one",
        tenant_id="tenant-1",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_site_members_graph_returns_400_for_invalid_request() -> None:
    service = MagicMock()
    service.get_site_members = AsyncMock(side_effect=ValueError("bad site"))

    response = await sharepoint.get_site_members(
        site_hostname="contoso.sharepoint.com",
        site_path="/sites/site-one",
        tenant_id="tenant-1",
        sharepoint_service=service,
        logger=MagicMock(),
    )

    assert response.status_code == 400
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["error"] == "Invalid members request"
    assert payload["details"] == "bad site"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_site_returns_success_payload() -> None:
    service = MagicMock()
    service.sync_site = AsyncMock(return_value=MagicMock(model_dump=MagicMock(return_value={"copied": 1, "failed": 0})))

    response = await sharepoint.sync_site(
        request_body=SharePointSyncRequest(),
        sharepoint_service=service,
        bearer_token=None,
        logger=MagicMock(),
    )

    assert response.status_code == 200
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["message"] == "SharePoint sync completed"
    assert payload["data"]["copied"] == 1
    service.sync_site.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_site_returns_400_for_invalid_request() -> None:
    service = MagicMock()
    service.sync_site = AsyncMock(side_effect=ValueError("bad request"))

    response = await sharepoint.sync_site(
        request_body=SharePointSyncRequest(),
        sharepoint_service=service,
        bearer_token=None,
        logger=MagicMock(),
    )

    assert response.status_code == 400
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["error"] == "Invalid SharePoint sync request"
    assert payload["details"] == "bad request"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_site_passes_delegated_bearer_token_to_service() -> None:
    service = MagicMock()
    request = SharePointSyncRequest()
    service.sync_site = AsyncMock(return_value=MagicMock(model_dump=MagicMock(return_value={"copied": 1, "failed": 0})))

    response = await sharepoint.sync_site(
        request_body=request,
        sharepoint_service=service,
        bearer_token="delegated-token-123",
        logger=MagicMock(),
    )

    assert response.status_code == 200
    service.sync_site.assert_awaited_once()
    args, kwargs = service.sync_site.await_args
    assert args[0] is request
    assert kwargs["delegated_graph_access_token"] == "delegated-token-123"
