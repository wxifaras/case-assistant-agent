#!/usr/bin/env python3
"""Create or reuse an Entra app registration and service principal.

This helper complements ``scripts/setup_rbac.py``.
It creates the Microsoft Entra application registration and service principal
that ``setup_rbac.py`` expects when assigning Azure RBAC roles and optional
Microsoft Graph application permissions.

Examples:
    python scripts/create_service_principal.py --name case-assistant-sharepoint-sync

    python scripts/create_service_principal.py \
        --name case-assistant-sharepoint-sync \
        --tenant-id <tenant-id> \
        --subscription <subscription-id-or-name> \
        --create-secret
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any


def _run(args: list[str], capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=capture,
        text=True,
        shell=sys.platform == "win32",
    )


def _run_json(args: list[str]) -> tuple[bool, Any]:
    result = _run(args)
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    try:
        return True, json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, result.stdout.strip()


def _run_str(args: list[str]) -> tuple[bool, str]:
    result = _run(args)
    return result.returncode == 0, (result.stdout.strip() or result.stderr.strip())


def check_prereqs() -> None:
    ok, _ = _run_str(["az", "--version"])
    if not ok:
        print("Azure CLI is not installed or not on PATH.", file=sys.stderr)
        sys.exit(1)


def ensure_login(tenant_id: str | None) -> dict[str, Any]:
    ok, account = _run_json(["az", "account", "show"])
    if ok and isinstance(account, dict):
        return account

    cmd = ["az", "login"]
    if tenant_id:
        cmd += ["--tenant", tenant_id]

    result = _run(cmd, capture=False)
    if result.returncode != 0:
        print("az login failed.", file=sys.stderr)
        sys.exit(1)

    ok, account = _run_json(["az", "account", "show"])
    if not ok or not isinstance(account, dict):
        print("Could not retrieve account after login.", file=sys.stderr)
        sys.exit(1)
    return account


def select_subscription(subscription: str | None) -> dict[str, Any]:
    if subscription:
        ok, output = _run_str(["az", "account", "set", "--subscription", subscription])
        if not ok:
            print(f"Failed to select subscription '{subscription}': {output}", file=sys.stderr)
            sys.exit(1)

    ok, account = _run_json(["az", "account", "show"])
    if not ok or not isinstance(account, dict):
        print("Could not determine active subscription.", file=sys.stderr)
        sys.exit(1)
    return account


def find_app_by_name(display_name: str) -> dict[str, Any] | None:
    ok, result = _run_json(
        [
            "az",
            "ad",
            "app",
            "list",
            "--display-name",
            display_name,
            "-o",
            "json",
        ]
    )
    if not ok or not isinstance(result, list):
        return None
    for item in result:
        if isinstance(item, dict) and item.get("displayName") == display_name:
            return item
    return None


def create_app(display_name: str, sign_in_audience: str) -> dict[str, Any]:
    ok, result = _run_json(
        [
            "az",
            "ad",
            "app",
            "create",
            "--display-name",
            display_name,
            "--sign-in-audience",
            sign_in_audience,
            "-o",
            "json",
        ]
    )
    if not ok or not isinstance(result, dict):
        print(f"Failed to create app registration: {result}", file=sys.stderr)
        sys.exit(1)
    return result


def ensure_app(display_name: str, sign_in_audience: str) -> tuple[dict[str, Any], bool]:
    existing = find_app_by_name(display_name)
    if existing is not None:
        return existing, False
    return create_app(display_name, sign_in_audience), True


def get_service_principal(app_id: str) -> dict[str, Any] | None:
    ok, result = _run_json(["az", "ad", "sp", "show", "--id", app_id, "-o", "json"])
    if ok and isinstance(result, dict):
        return result
    return None


def create_service_principal(app_id: str) -> dict[str, Any]:
    ok, result = _run_json(["az", "ad", "sp", "create", "--id", app_id, "-o", "json"])
    if not ok or not isinstance(result, dict):
        print(f"Failed to create service principal: {result}", file=sys.stderr)
        sys.exit(1)
    return result


def ensure_service_principal(app_id: str) -> tuple[dict[str, Any], bool]:
    existing = get_service_principal(app_id)
    if existing is not None:
        return existing, False
    return create_service_principal(app_id), True


def create_secret(app_id: str, years: int) -> dict[str, Any]:
    ok, result = _run_json(
        [
            "az",
            "ad",
            "app",
            "credential",
            "reset",
            "--id",
            app_id,
            "--append",
            "--display-name",
            "case-assistant-generated-secret",
            "--years",
            str(years),
            "-o",
            "json",
        ]
    )
    if not ok or not isinstance(result, dict):
        print(f"Failed to create client secret: {result}", file=sys.stderr)
        sys.exit(1)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or reuse an Entra app registration and service principal.")
    parser.add_argument("--name", required=True, help="Display name for the app registration and service principal.")
    parser.add_argument("--tenant-id", help="Tenant id to use for az login.")
    parser.add_argument("--subscription", help="Subscription name or id to select before printing next steps.")
    parser.add_argument(
        "--sign-in-audience",
        default="AzureADMyOrg",
        choices=["AzureADMyOrg", "AzureADMultipleOrgs"],
        help="Entra sign-in audience for the app registration.",
    )
    parser.add_argument(
        "--create-secret",
        action="store_true",
        help="Create and print a new client secret for the app registration.",
    )
    parser.add_argument(
        "--secret-years",
        type=int,
        default=1,
        help="Number of years before the generated secret expires (default: 1).",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print machine-readable JSON only.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    check_prereqs()
    account = ensure_login(args.tenant_id)
    account = select_subscription(args.subscription)

    app, app_created = ensure_app(args.name, args.sign_in_audience)
    app_id = str(app.get("appId") or "")
    app_object_id = str(app.get("id") or "")
    if not app_id or not app_object_id:
        print("App registration result did not contain appId/id.", file=sys.stderr)
        sys.exit(1)

    service_principal, sp_created = ensure_service_principal(app_id)
    service_principal_object_id = str(service_principal.get("id") or "")
    if not service_principal_object_id:
        print("Service principal result did not contain id.", file=sys.stderr)
        sys.exit(1)

    secret_value: str | None = None
    if args.create_secret:
        secret = create_secret(app_id, years=args.secret_years)
        secret_value = str(secret.get("password") or "") or None

    summary = {
        "tenant_id": account.get("tenantId"),
        "subscription_id": account.get("id"),
        "subscription_name": account.get("name"),
        "display_name": args.name,
        "app_created": app_created,
        "service_principal_created": sp_created,
        "app_id": app_id,
        "app_object_id": app_object_id,
        "service_principal_object_id": service_principal_object_id,
        "client_secret_created": bool(secret_value),
        "client_secret": secret_value,
    }

    if args.print_json:
        print(json.dumps(summary, indent=2))
        return

    print("App registration ready")
    print(f"  display name: {args.name}")
    print(f"  tenant id: {summary['tenant_id']}")
    print(f"  subscription: {summary['subscription_name']} ({summary['subscription_id']})")
    print(f"  app created: {app_created}")
    print(f"  service principal created: {sp_created}")
    print(f"  app id (client id): {app_id}")
    print(f"  app object id: {app_object_id}")
    print(f"  service principal object id: {service_principal_object_id}")

    if secret_value:
        print("  client secret: CREATED")
        print(f"  client secret value: {secret_value}")
    else:
        print("  client secret: not created")

    print("\nNext step:")
    print(
        "  python scripts/setup_rbac.py "
        f"--subscription \"{summary['subscription_id']}\" "
        "--resource-group <resource-group> "
        f"--principal-id {service_principal_object_id} "
        "--principal-type ServicePrincipal "
        "--grant-sharepoint-app-permissions"
    )


if __name__ == "__main__":
    main()
