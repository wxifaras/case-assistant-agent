"""JWT validation and OBO token exchange for the Case Assistant Agent API.

Provides FastAPI dependencies and helper functions for:
- Validating RS256 bearer tokens via JWKS
- On-Behalf-Of (OBO) token exchange for delegated user flows
- App-only / managed-identity passthrough when no bearer token is present
"""

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import jwt
from azure.identity.aio import OnBehalfOfCredential
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError

from app.api.dependencies import get_settings
from app.core.settings import Settings

_optional_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _csv_values(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


@lru_cache(maxsize=16)
def _jwks_client(tenant_id: str) -> PyJWKClient:
    return PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")


def _expected_audiences(settings: Settings) -> set[str]:
    audiences = _csv_values(settings.api.auth_audience)
    if settings.azure_client_id:
        audiences.add(settings.azure_client_id)
        audiences.add(f"api://{settings.azure_client_id}")
    return {a for a in audiences if a}


def _is_delegated_token(claims: Mapping[str, Any]) -> bool:
    return bool(str(claims.get("scp") or "").strip())


def _validate_access_token(token: str, settings: Settings) -> dict[str, Any]:
    tenant_id = settings.azure_tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AZURE_TENANT_ID is required when API auth is enabled.",
        )

    audiences = _expected_audiences(settings)
    if not audiences:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No API audiences configured for JWT validation.",
        )

    valid_issuers = [
        f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        f"https://sts.windows.net/{tenant_id}/",
    ]

    try:
        signing_key = _jwks_client(tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            audience=list(audiences),
            issuer=valid_issuers,
            options={"require": ["exp", "iss", "aud"]},
        )
    except InvalidTokenError as ex:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid bearer token: {ex}",
        ) from ex

    if not _is_delegated_token(claims):
        allowed_client_ids = _csv_values(settings.api.allowed_app_client_ids)
        if allowed_client_ids:
            app_id = str(claims.get("azp") or claims.get("appid") or "").strip()
            if app_id not in allowed_client_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="App caller is not allowed for this endpoint.",
                )

    return claims


async def _exchange_graph_token_obo(user_assertion: str, settings: Settings) -> str:
    if not settings.api.obo_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Delegated user tokens require OBO, but API_OBO_ENABLED=true is not set. Set API_OBO_ENABLED=true in .env.",
        )

    if not settings.azure_tenant_id or not settings.azure_client_id or not settings.azure_client_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET are required for OBO token exchange.",
        )

    credential = OnBehalfOfCredential(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        client_secret=settings.azure_client_secret,
        user_assertion=user_assertion,
    )
    try:
        token = await credential.get_token(settings.api.obo_graph_scope)
        return token.token
    except Exception as ex:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Failed to exchange delegated token via OBO: {ex}",
        ) from ex
    finally:
        await credential.close()


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def get_optional_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> str | None:
    """Return a bearer token from ``Authorization`` when present.

    This dependency is intentionally permissive and does not validate JWT
    signatures or claims. It only extracts the token so specific endpoints can
    choose delegated Graph execution paths while preserving backward-compatible
    app-identity fallback behavior.
    """
    if credentials is None:
        return None
    token = (credentials.credentials or "").strip()
    return token or None


async def get_sync_graph_access_token(
    settings: Settings = Depends(get_settings),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> str | None:
    """Resolve the Graph access token used by SharePoint sync endpoints.

    Behavior:
    - ``require_jwt_validation=false`` (dev mode): passthrough bearer token unchanged.
    - ``require_jwt_validation=true`` + delegated caller: validate JWT, then exchange
      via OBO for a Graph-scoped token.
    - ``require_jwt_validation=true`` + no token: return ``None`` so the sync service
      falls back to ``DefaultAzureCredential`` (app-only / managed-identity path).
    - ``require_jwt_validation=true`` + app JWT (no ``scp`` claim): validate JWT, then
      return ``None`` → ``DefaultAzureCredential`` fallback.
    """
    token = (credentials.credentials or "").strip() if credentials else None

    if not settings.api.require_jwt_validation:
        return token or None

    # No bearer token → app-only / managed-identity path; let the sync service
    # use DefaultAzureCredential internally.
    if not token:
        return None

    claims = _validate_access_token(token, settings)
    if _is_delegated_token(claims):
        return await _exchange_graph_token_obo(token, settings)

    return None
