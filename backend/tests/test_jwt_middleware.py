"""Unit tests for app.api.jwt_middleware.

Covers the get_sync_graph_access_token dependency for all execution paths:
- Passthrough (require_jwt_validation=false): bearer token returned unchanged.
- Passthrough with no token (require_jwt_validation=false): returns None.
- App-only (require_jwt_validation=true, no token): returns None for DefaultAzureCredential fallback.
- Delegated (require_jwt_validation=true, valid JWT with scp): OBO exchange called.
- Invalid JWT (require_jwt_validation=true): raises HTTP 401.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.jwt_middleware import get_sync_graph_access_token
from app.core.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(*, require_jwt_validation: bool = False, obo_enabled: bool = False) -> Settings:
    """Return a minimal Settings object with the given JWT flags."""
    settings = MagicMock(spec=Settings)
    settings.api.require_jwt_validation = require_jwt_validation
    settings.api.obo_enabled = obo_enabled
    settings.api.auth_audience = None
    settings.api.obo_graph_scope = "https://graph.microsoft.com/.default"
    settings.api.allowed_app_client_ids = None
    settings.azure_tenant_id = "tenant-id"
    settings.azure_client_id = "client-id"
    settings.azure_client_secret = "client-secret"
    return settings


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# Passthrough flow (require_jwt_validation=false)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_passthrough_returns_bearer_token_unchanged() -> None:
    """When JWT validation is disabled, the raw bearer token is returned as-is."""
    settings = _make_settings(require_jwt_validation=False)
    creds = _credentials("raw-token-abc")

    result = await get_sync_graph_access_token(settings=settings, credentials=creds)

    assert result == "raw-token-abc"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_passthrough_with_no_token_returns_none() -> None:
    """When JWT validation is disabled and no token is present, None is returned."""
    settings = _make_settings(require_jwt_validation=False)

    result = await get_sync_graph_access_token(settings=settings, credentials=None)

    assert result is None


# ---------------------------------------------------------------------------
# App-only flow (require_jwt_validation=true, no token)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_app_only_no_token_returns_none() -> None:
    """When validation is enabled but no bearer token is sent, None is returned so
    the sync service can fall back to DefaultAzureCredential."""
    settings = _make_settings(require_jwt_validation=True)

    result = await get_sync_graph_access_token(settings=settings, credentials=None)

    assert result is None


# ---------------------------------------------------------------------------
# Delegated flow (require_jwt_validation=true, valid JWT with scp claim)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegated_flow_calls_obo_and_returns_graph_token() -> None:
    """When a valid delegated JWT is supplied and OBO is enabled, the Graph token
    returned by OnBehalfOfCredential is forwarded to the caller."""
    settings = _make_settings(require_jwt_validation=True, obo_enabled=True)
    creds = _credentials("delegated-jwt")

    delegated_claims = {
        "scp": "Sites.Read.All",
        "iss": f"https://sts.windows.net/{settings.azure_tenant_id}/",
        "aud": f"api://{settings.azure_client_id}",
        "exp": 9999999999,
    }

    with (
        patch("app.api.jwt_middleware._validate_access_token", return_value=delegated_claims),
        patch(
            "app.api.jwt_middleware._exchange_graph_token_obo",
            new=AsyncMock(return_value="graph-access-token"),
        ) as mock_obo,
    ):
        result = await get_sync_graph_access_token(settings=settings, credentials=creds)

    assert result == "graph-access-token"
    mock_obo.assert_awaited_once_with("delegated-jwt", settings)


# ---------------------------------------------------------------------------
# Invalid JWT (require_jwt_validation=true, bad token)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_jwt_raises_401() -> None:
    """When validation is enabled and the token fails JWKS verification, HTTP 401
    is raised."""
    settings = _make_settings(require_jwt_validation=True)
    creds = _credentials("invalid-or-expired-jwt")

    with patch(
        "app.api.jwt_middleware._validate_access_token",
        side_effect=HTTPException(status_code=401, detail="Invalid bearer token: Signature verification failed"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_sync_graph_access_token(settings=settings, credentials=creds)

    assert exc_info.value.status_code == 401
    assert "Invalid bearer token" in exc_info.value.detail
