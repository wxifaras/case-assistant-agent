#!/usr/bin/env python3
"""Test auth flows against the local API.

Flows
-----
delegated   Opens browser for login, acquires a user token, exchanges it via
            OBO for a Graph token (requires API_REQUIRE_JWT_VALIDATION=true +
            API_OBO_ENABLED=true).

app-only    Sends no bearer token; the server falls back to
            DefaultAzureCredential (managed identity / env credentials).

passthrough Sends a raw bearer token without JWT validation; the server
            forwards it directly to Graph (requires
            API_REQUIRE_JWT_VALIDATION=false).

both        Runs all three flows in sequence.

Usage
-----
    python scripts/test_auth_flows.py --flow delegated
    python scripts/test_auth_flows.py --flow app-only
    python scripts/test_auth_flows.py --flow passthrough
    python scripts/test_auth_flows.py --flow both

    # Override defaults via args or env vars:
    python scripts/test_auth_flows.py --flow delegated \
        --client-id <app-id> --tenant-id <tenant-id> \
        --api-base http://myserver:8000 \
        --site-url https://contoso.sharepoint.com/sites/MySite

    Environment variables (fallback when flag is not supplied):
        TEST_CLIENT_ID, TEST_TENANT_ID, TEST_API_BASE, TEST_SYNC_SITE_PATH, TEST_SITE_URL
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx

_DEFAULT_CLIENT_ID = os.environ.get("TEST_CLIENT_ID", "49fa46df-0f84-4331-a46e-990c7cfdd80b")
_DEFAULT_TENANT_ID = os.environ.get("TEST_TENANT_ID", "e4b2fabf-25d1-4983-b404-b12926058ab7")
_DEFAULT_API_BASE = os.environ.get("TEST_API_BASE", "http://localhost:8000")
_DEFAULT_SYNC_SITE_PATH = os.environ.get("TEST_SYNC_SITE_PATH", "/api/sharepoint/sites/sync-site")
_DEFAULT_SITE_URL = os.environ.get(
    "TEST_SITE_URL",
    "https://mngenvmcap982280.sharepoint.com/sites/IRISSoftwaKMAutomatiKM01",
)


def get_delegated_token(client_id: str, tenant_id: str, scope: str) -> str:
    from azure.identity import InteractiveBrowserCredential  # type: ignore[import]

    print(f"\n[Delegated] Opening browser for login (scope: {scope})")
    cred = InteractiveBrowserCredential(client_id=client_id, tenant_id=tenant_id)
    token = cred.get_token(scope)
    print("[Delegated] Token acquired successfully.")
    return token.token


async def call_sync_site(
    bearer_token: str | None,
    label: str,
    *,
    api_base: str,
    sync_site_path: str,
    site_url: str,
) -> None:
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    print(f"\n[{label}] POST {api_base}{sync_site_path}")
    print(f"[{label}] Authorization header: {'Bearer <token>' if bearer_token else '(none)'}")
    print(f"[{label}] Sending request...", flush=True)

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(
                f"{api_base}{sync_site_path}",
                headers=headers,
                json={"site_url": site_url},
            )
    except httpx.ConnectError as exc:
        print(f"[{label}] ❌ FAIL — connection error (is the server running on {api_base}?): {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[{label}] ❌ FAIL — {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[{label}] HTTP {resp.status_code}")
    try:
        body = resp.json()
        print(f"[{label}] Response: {json.dumps(body, indent=2)}")
    except Exception:
        print(f"[{label}] Response text: {resp.text[:500]}")

    if resp.status_code < 300:
        print(f"[{label}] ✅ PASS")
    else:
        print(f"[{label}] ❌ FAIL (HTTP {resp.status_code})")


async def run(
    flow: str, client_id: str, tenant_id: str, scope: str, api_base: str, sync_site_path: str, site_url: str
) -> None:
    kwargs = {"api_base": api_base, "sync_site_path": sync_site_path, "site_url": site_url}

    if flow in ("app-only", "both"):
        print("\n=== Test: App-only flow (no bearer token) ===")
        await call_sync_site(None, "app-only", **kwargs)

    if flow in ("passthrough", "both"):
        print("\n=== Test: Passthrough flow (raw token, no OBO) ===")
        print("NOTE: requires API_REQUIRE_JWT_VALIDATION=false on the server")
        print("NOTE: acquires Graph-scoped token directly (audience=graph.microsoft.com)")
        try:
            # Passthrough forwards the token raw to Graph, so it must carry Graph audience.
            # The delegated (OBO) flow uses the app scope and lets the server exchange it.
            graph_scope = "https://graph.microsoft.com/Sites.Read.All"
            token = get_delegated_token(client_id, tenant_id, graph_scope)
            await call_sync_site(token, "passthrough", **kwargs)
        except ImportError:
            print("azure-identity not installed. Run: pip install azure-identity", file=sys.stderr)
            sys.exit(1)

    if flow in ("delegated", "both"):
        print("\n=== Test: Delegated flow (Interactive Browser + OBO) ===")
        try:
            token = get_delegated_token(client_id, tenant_id, scope)
            await call_sync_site(token, "delegated", **kwargs)
        except ImportError:
            print("azure-identity not installed. Run: pip install azure-identity", file=sys.stderr)
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test auth flows against the local API.")
    parser.add_argument(
        "--flow",
        choices=["delegated", "app-only", "passthrough", "both"],
        default="both",
        help="Which flow to test.",
    )
    parser.add_argument(
        "--client-id", default=_DEFAULT_CLIENT_ID, metavar="UUID", help="Azure AD app (client) ID. Env: TEST_CLIENT_ID"
    )
    parser.add_argument(
        "--tenant-id", default=_DEFAULT_TENANT_ID, metavar="UUID", help="Azure AD tenant ID. Env: TEST_TENANT_ID"
    )
    parser.add_argument(
        "--scope", default=None, metavar="SCOPE", help="OAuth2 scope (default: api://<client-id>/access_as_user)"
    )
    parser.add_argument("--api-base", default=_DEFAULT_API_BASE, metavar="URL", help="API base URL. Env: TEST_API_BASE")
    parser.add_argument(
        "--sync-site-path",
        default=_DEFAULT_SYNC_SITE_PATH,
        metavar="PATH",
        help="Sync endpoint path. Env: TEST_SYNC_SITE_PATH",
    )
    parser.add_argument(
        "--site-url", default=_DEFAULT_SITE_URL, metavar="URL", help="SharePoint site URL to sync. Env: TEST_SITE_URL"
    )
    args = parser.parse_args()

    scope = args.scope or f"api://{args.client_id}/access_as_user"
    asyncio.run(
        run(args.flow, args.client_id, args.tenant_id, scope, args.api_base, args.sync_site_path, args.site_url)
    )


if __name__ == "__main__":
    main()
