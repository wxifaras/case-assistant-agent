#!/usr/bin/env python3
"""
Setup Cosmos DB RBAC

Sets up a Cosmos DB data-plane RBAC role definition and assignment.

Creates a custom role with full data-plane permissions and assigns it to a
specified principal (user, managed identity, or service principal).
Defaults to the currently signed-in Azure CLI user.

Usage:
    python setup_cosmos_rbac.py

    # Non-interactive (all values supplied up-front):
    python setup_cosmos_rbac.py \\
        --resource-group myRG \\
        --account-name myCosmosAccount

    # Assign to a specific principal instead of the signed-in user:
    python setup_cosmos_rbac.py \\
        --resource-group myRG \\
        --account-name myCosmosAccount \\
        --principal-id 12345678-1234-1234-1234-123456789abc

    # Get your user object ID:
    az ad signed-in-user show --query id -o tsv

    # Get a managed identity principal ID:
    az identity show --name <identity-name> --resource-group <rg> --query principalId -o tsv
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Optional

# ---------------------------------------------------------------------------
# Terminal colour helpers (ANSI; work on all modern terminals including Windows
# with Virtual Terminal Processing enabled, which is the default since Win 10)
# ---------------------------------------------------------------------------

RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
WHITE = "\033[97m"


def _c(colour: str, text: str) -> str:
    """Wrap *text* with an ANSI colour code if stdout is a TTY."""
    if sys.stdout.isatty():
        return f"{colour}{text}{RESET}"
    return text


def print_header(msg: str) -> None:
    width = max(len(msg) + 4, 66)
    print(_c(CYAN, "\n" + "═" * width))
    print(_c(CYAN, f"  {msg}"))
    print(_c(CYAN, "═" * width))


def print_section(msg: str) -> None:
    print(_c(CYAN, f"\n─── {msg} ───"))


def print_step(msg: str) -> None:
    print(_c(CYAN, f"  › {msg}"))


def print_success(msg: str) -> None:
    print(_c(GREEN, f"  \u2713 {msg}"))


def print_warning(msg: str) -> None:
    print(_c(YELLOW, f"  \u26a0 {msg}"))


def print_error(msg: str) -> None:
    print(_c(RED, f"  \u2717 {msg}"), file=sys.stderr)


def print_detail(msg: str) -> None:
    print(f"    {msg}")


# ---------------------------------------------------------------------------
# Azure CLI helpers
# ---------------------------------------------------------------------------


def _run(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run an Azure CLI command and return the CompletedProcess."""
    # On Windows, `az` is a .cmd wrapper; shell=True is required to locate it.
    return subprocess.run(
        args,
        capture_output=capture,
        text=True,
        shell=sys.platform == "win32",
    )


def _run_json(args: list[str]) -> tuple[bool, object]:
    """
    Run an Azure CLI command and return (success, parsed_json_output).
    Returns (False, None) on non-zero exit or JSON parse failure.
    """
    result = _run(args)
    if result.returncode != 0:
        return False, result.stderr.strip()
    try:
        return True, json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, result.stdout.strip()


def _run_str(args: list[str]) -> tuple[bool, str]:
    result = _run(args)
    return result.returncode == 0, result.stdout.strip()


# ---------------------------------------------------------------------------
# Auth / subscription helpers
# ---------------------------------------------------------------------------


def check_prereqs() -> None:
    print_section("Prerequisites")
    result = _run(["az", "--version"])
    if result.returncode != 0:
        print_error("Azure CLI is not installed. Install from https://aka.ms/azure-cli")
        sys.exit(1)
    print_success("Azure CLI found")


def ensure_login(tenant_id: Optional[str]) -> None:
    print_section("Authentication")
    ok, account = _run_json(["az", "account", "show"])
    if ok and isinstance(account, dict):
        user = account.get("user", {}).get("name", "<unknown>")
        print_success(f"Already signed in as: {_c(WHITE, user)}")
        return
    print_step("Not signed in — launching az login...")
    login_cmd = ["az", "login"]
    if tenant_id:
        login_cmd += ["--tenant", tenant_id]
    result = _run(login_cmd, capture=False)
    if result.returncode != 0:
        print_error("Login failed.")
        sys.exit(1)


def select_subscription(subscription_arg: Optional[str]) -> str:
    print_section("Subscription")
    ok, subs = _run_json(["az", "account", "list", "--query", "[].{id:id, name:name, state:state}", "-o", "json"])
    if not ok or not isinstance(subs, list):
        print_error("Could not list subscriptions.")
        sys.exit(1)
    active_subs = [s for s in subs if s.get("state") == "Enabled"]
    if not active_subs:
        print_error("No enabled subscriptions found.")
        sys.exit(1)
    if subscription_arg:
        for s in active_subs:
            if s["id"] == subscription_arg or s["name"] == subscription_arg:
                _run_str(["az", "account", "set", "--subscription", s["id"]])
                print_success(f"Using subscription: {s['name']} ({s['id']})")
                return s["id"]
        print_error(f"Subscription '{subscription_arg}' not found or not enabled.")
        sys.exit(1)
    if len(active_subs) == 1:
        sub = active_subs[0]
        _run_str(["az", "account", "set", "--subscription", sub["id"]])
        print_success(f"Using subscription: {sub['name']} ({sub['id']})")
        return sub["id"]
    print()
    print(_c(WHITE, "  Available subscriptions:"))
    for i, s in enumerate(active_subs, 1):
        print(f"    [{i}] {s['name']}")
        print(f"        {s['id']}")
    while True:
        try:
            choice = input(_c(CYAN, "\n  Select subscription number: ")).strip()
            idx = int(choice) - 1
            if 0 <= idx < len(active_subs):
                sub = active_subs[idx]
                _run_str(["az", "account", "set", "--subscription", sub["id"]])
                print_success(f"Using subscription: {sub['name']} ({sub['id']})")
                return sub["id"]
        except (ValueError, KeyboardInterrupt):
            pass
        print_warning("Invalid selection, try again.")


def get_current_user_id() -> str:
    print_section("Signed-in user identity")
    ok, uid = _run_str(["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"])
    if not ok or not uid:
        print_error("Could not retrieve signed-in user object ID.")
        sys.exit(1)
    print_success(f"User object ID: {_c(WHITE, uid)}")
    return uid


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_prerequisites() -> None:
    pass  # handled by check_prereqs()


def validate_auth() -> dict:
    ok, account = _run_json(["az", "account", "show"])
    if not ok or not isinstance(account, dict):
        print_error("Not authenticated with Azure.")
        sys.exit(1)
    return account


def validate_cosmos_account(resource_group: str, account_name: str) -> dict:
    print_section(f"Cosmos DB  [{account_name}]")

    ok, cosmos = _run_json(
        [
            "az",
            "cosmosdb",
            "show",
            "--name",
            account_name,
            "--resource-group",
            resource_group,
        ]
    )
    if not ok or not isinstance(cosmos, dict):
        print_error(f"Cosmos DB account '{account_name}' not found in resource group '{resource_group}'")
        sys.exit(1)

    endpoint = cosmos.get("documentEndpoint", "<unknown>")
    print_success(f"Found Cosmos DB account: {account_name}")
    print_detail(f"Endpoint: {endpoint}")
    return cosmos


# ---------------------------------------------------------------------------
# Role definition
# ---------------------------------------------------------------------------


def _delete_role_assignments_for_role(resource_group: str, account_name: str, role_id: str) -> None:
    """Delete all assignments referencing a given role definition ID."""
    ok, assignments = _run_json(
        [
            "az",
            "cosmosdb",
            "sql",
            "role",
            "assignment",
            "list",
            "--account-name",
            account_name,
            "--resource-group",
            resource_group,
            "--query",
            f"[?contains(roleDefinitionId, '{role_id}')]",
        ]
    )
    if not ok or not isinstance(assignments, list):
        return
    for a in assignments:
        aid = a.get("name")
        if not aid:
            continue
        print_step(f"Deleting role assignment {aid}...")
        result = _run(
            [
                "az",
                "cosmosdb",
                "sql",
                "role",
                "assignment",
                "delete",
                "--account-name",
                account_name,
                "--resource-group",
                resource_group,
                "--role-assignment-id",
                aid,
                "--yes",
            ]
        )
        if result.returncode != 0:
            print_error(f"Failed to delete assignment {aid}: {result.stderr.strip()}")
            sys.exit(1)
        print_success(f"Deleted assignment {aid}")


def _delete_role_definition(resource_group: str, account_name: str, role_id: str) -> None:
    print_step(f"Deleting role definition {role_id}...")
    result = _run(
        [
            "az",
            "cosmosdb",
            "sql",
            "role",
            "definition",
            "delete",
            "--account-name",
            account_name,
            "--resource-group",
            resource_group,
            "--id",
            role_id,
            "--yes",
        ]
    )
    if result.returncode != 0:
        print_error(f"Failed to delete role definition {role_id}: {result.stderr.strip()}")
        sys.exit(1)
    print_success("Role definition deleted")


def ensure_role_definition(
    resource_group: str,
    account_name: str,
    role_name: str,
    force: bool = False,
) -> str:
    """Return the role definition ID, creating the role if it does not exist.

    When ``force`` is True, any existing role definition with the same name
    (and all assignments referencing it) is deleted first, then recreated.
    """
    print_section("Role Definition")
    print_step("Checking for existing role definition...")

    ok, existing = _run_json(
        [
            "az",
            "cosmosdb",
            "sql",
            "role",
            "definition",
            "list",
            "--account-name",
            account_name,
            "--resource-group",
            resource_group,
            "--query",
            f"[?roleName=='{role_name}']",
        ]
    )

    if ok and isinstance(existing, list) and existing:
        role_id = existing[0]["name"]
        if not force:
            print_warning(f"Role '{role_name}' already exists — reusing")
            print_detail(f"Role ID: {role_id}")
            return role_id
        print_warning(f"--force specified: deleting existing role '{role_name}' and its assignments")
        _delete_role_assignments_for_role(resource_group, account_name, role_id)
        _delete_role_definition(resource_group, account_name, role_id)

    print_step("Creating custom role definition...")
    role_body = {
        "RoleName": role_name,
        "Type": "CustomRole",
        "AssignableScopes": ["/"],
        "Permissions": [
            {
                "DataActions": [
                    "Microsoft.DocumentDB/databaseAccounts/readMetadata",
                    "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create",
                    "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read",
                    "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/delete",
                    "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/upsert",
                    "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/replace",
                    "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/executeQuery",
                    "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/write",
                    "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/write",
                ]
            }
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(role_body, tmp, indent=2)
        tmp_path = tmp.name

    try:
        ok, result = _run_json(
            [
                "az",
                "cosmosdb",
                "sql",
                "role",
                "definition",
                "create",
                "--account-name",
                account_name,
                "--resource-group",
                resource_group,
                "--body",
                f"@{tmp_path}",
            ]
        )
    finally:
        os.unlink(tmp_path)

    if not ok or not isinstance(result, dict):
        print_error(f"Failed to create role definition: {result}")
        sys.exit(1)

    role_id = result["name"]
    print_success(f"Role definition created: {role_name}")
    print_detail(f"Role ID: {role_id}")
    return role_id


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------


def ensure_role_assignment(
    resource_group: str,
    account_name: str,
    principal_id: str,
    role_definition_id: str,
    force: bool = False,
) -> None:
    print_section("Role Assignment")
    print_step("Checking for existing role assignment...")

    ok, existing = _run_json(
        [
            "az",
            "cosmosdb",
            "sql",
            "role",
            "assignment",
            "list",
            "--account-name",
            account_name,
            "--resource-group",
            resource_group,
            "--query",
            f"[?principalId=='{principal_id}' && contains(roleDefinitionId, '{role_definition_id}')]",
        ]
    )

    if ok and isinstance(existing, list) and existing:
        if not force:
            print_warning(f"Role already assigned to principal {principal_id}")
            print_detail(f"Assignment ID: {existing[0]['name']}")
            return
        print_warning(f"--force specified: deleting existing assignment(s) for principal {principal_id}")
        for a in existing:
            aid = a.get("name")
            if not aid:
                continue
            result = _run(
                [
                    "az",
                    "cosmosdb",
                    "sql",
                    "role",
                    "assignment",
                    "delete",
                    "--account-name",
                    account_name,
                    "--resource-group",
                    resource_group,
                    "--role-assignment-id",
                    aid,
                    "--yes",
                ]
            )
            if result.returncode != 0:
                print_error(f"Failed to delete assignment {aid}: {result.stderr.strip()}")
                sys.exit(1)
            print_success(f"Deleted assignment {aid}")

    print_step("Assigning role to principal...")
    ok, assignment = _run_json(
        [
            "az",
            "cosmosdb",
            "sql",
            "role",
            "assignment",
            "create",
            "--account-name",
            account_name,
            "--resource-group",
            resource_group,
            "--scope",
            "/",
            "--principal-id",
            principal_id,
            "--role-definition-id",
            role_definition_id,
        ]
    )

    if not ok or not isinstance(assignment, dict):
        print_error(f"Failed to create role assignment: {assignment}")
        sys.exit(1)

    print_success("Role assigned successfully")
    print_detail(f"Assignment ID: {assignment['name']}")
    print_detail(f"Principal ID:  {assignment['principalId']}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup_cosmos_rbac.py",
        description=(
            "Creates a custom Cosmos DB data-plane RBAC role and assigns it "
            "to the currently signed-in user (or a specified principal)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tenant-id", "-t", metavar="TENANT_ID", help="Entra ID tenant ID (used if login is needed).")
    parser.add_argument("--subscription", "-s", metavar="SUBSCRIPTION", help="Subscription name or ID.")
    parser.add_argument(
        "--resource-group", "-g", metavar="RESOURCE_GROUP", help="Resource group containing the Cosmos DB account."
    )
    parser.add_argument("--account-name", "-a", metavar="ACCOUNT_NAME", help="Name of the Cosmos DB account.")
    parser.add_argument(
        "--principal-id",
        "-p",
        metavar="PRINCIPAL_ID",
        help="Object ID of the principal to assign the role to (defaults to signed-in user).",
    )
    parser.add_argument(
        "--role-name",
        "-r",
        default="CosmosDB-DataPlane-FullAccess",
        metavar="ROLE_NAME",
        help="Name for the custom role (default: CosmosDB-DataPlane-FullAccess).",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help=(
            "Delete and recreate the role definition (and all its assignments) "
            "plus any existing assignment for the target principal. Use this to "
            "reconcile drift in dataActions."
        ),
    )
    return parser


def _prompt_if_missing(current: Optional[str], prompt: str) -> Optional[str]:
    if current:
        return current
    value = input(_c(CYAN, f"  {prompt} (leave blank to skip): ")).strip()
    return value or None


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    print_header("Cosmos DB RBAC Setup")

    try:
        check_prereqs()
        ensure_login(args.tenant_id)
        select_subscription(args.subscription)

        # Resolve the signed-in user as the default principal
        principal_id = args.principal_id or get_current_user_id()

        print_section("Resources to configure")
        print(_c(WHITE, "  Leave blank to skip.\n"))

        resource_group = _prompt_if_missing(args.resource_group, "Resource group name")
        if not resource_group:
            print_warning("No resource group provided — nothing to configure.")
            sys.exit(0)

        account_name = _prompt_if_missing(args.account_name, "Cosmos DB account name")
        if not account_name:
            print_warning("No Cosmos DB account name provided — nothing to configure.")
            sys.exit(0)

        cosmos_account = validate_cosmos_account(resource_group, account_name)

        role_id = ensure_role_definition(resource_group, account_name, args.role_name, force=args.force)
        ensure_role_assignment(resource_group, account_name, principal_id, role_id, force=args.force)

        endpoint = cosmos_account.get("documentEndpoint", "<unknown>")
        print_header("Setup complete")
        print()
        print(_c(CYAN, "  Role propagation can take up to 5 minutes."))
        print()
        print(_c(WHITE, "  Add to .env:"))
        print(_c(WHITE, f"  COSMOS_ENDPOINT={endpoint}"))
        print()

    except KeyboardInterrupt:
        print()
        print_error("Interrupted by user.")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print_header("Setup Failed")
        print()
        print_error(f"Error: {exc}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
