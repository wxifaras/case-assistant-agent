#!/usr/bin/env python3
"""
Check Cosmos DB RBAC

Read-only verification that a Cosmos DB custom data-plane role exists and is
assigned to a given principal. Also prints the role's dataActions for review.

Companion to setup_cosmos_rbac.py — does NOT make any changes.

Usage:
    # Check signed-in user against the default role name
    python scripts/check_cosmos_rbac.py \\
        --resource-group myRG \\
        --account-name myCosmosAccount

    # Check a specific principal (managed identity / service principal)
    python scripts/check_cosmos_rbac.py \\
        --resource-group myRG \\
        --account-name myCosmosAccount \\
        --principal-id 12345678-1234-1234-1234-123456789abc

    # Check a different role name
    python scripts/check_cosmos_rbac.py -g myRG -a myCosmosAccount -r MyCustomRole

Exit codes:
    0 — role exists AND is assigned to the principal
    1 — role exists but is NOT assigned to the principal
    2 — role does not exist on the account, or a CLI/lookup error occurred
"""

import argparse
import json
import subprocess
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Terminal colour helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
WHITE = "\033[97m"


def _c(colour: str, text: str) -> str:
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


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        shell=sys.platform == "win32",
    )


def _run_json(args: list[str]) -> tuple[bool, object]:
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
# Checks
# ---------------------------------------------------------------------------


def check_prereqs() -> None:
    print_section("Prerequisites")
    result = _run(["az", "--version"])
    if result.returncode != 0:
        print_error("Azure CLI is not installed. Install from https://aka.ms/azure-cli")
        sys.exit(2)
    print_success("Azure CLI found")

    ok, _ = _run_json(["az", "account", "show"])
    if not ok:
        print_error("Not signed in to Azure. Run 'az login' first.")
        sys.exit(2)
    print_success("Signed in to Azure")


def get_signed_in_user_id() -> str:
    ok, uid = _run_str(["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"])
    if not ok or not uid:
        print_error("Could not retrieve signed-in user object ID.")
        sys.exit(2)
    return uid


def get_role_definition(resource_group: str, account_name: str, role_name: str) -> Optional[dict]:
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
    if not ok:
        print_error(f"Failed to query role definitions: {existing}")
        sys.exit(2)
    if not isinstance(existing, list) or not existing:
        return None
    return existing[0]


def get_assignments(resource_group: str, account_name: str, principal_id: str, role_id: str) -> list[dict]:
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
            f"[?principalId=='{principal_id}' && contains(roleDefinitionId, '{role_id}')]",
        ]
    )
    if not ok:
        print_error(f"Failed to list role assignments: {existing}")
        sys.exit(2)
    return existing if isinstance(existing, list) else []


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_cosmos_rbac.py",
        description="Verify a Cosmos DB data-plane RBAC role and assignment for a principal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--resource-group",
        "-g",
        required=True,
        metavar="RESOURCE_GROUP",
        help="Resource group containing the Cosmos DB account.",
    )
    parser.add_argument(
        "--account-name",
        "-a",
        required=True,
        metavar="ACCOUNT_NAME",
        help="Name of the Cosmos DB account.",
    )
    parser.add_argument(
        "--principal-id",
        "-p",
        metavar="PRINCIPAL_ID",
        help="Object ID of the principal to check (defaults to signed-in user).",
    )
    parser.add_argument(
        "--role-name",
        "-r",
        default="CosmosDB-DataPlane-FullAccess",
        metavar="ROLE_NAME",
        help="Name of the custom role to check (default: CosmosDB-DataPlane-FullAccess).",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    print_header("Cosmos DB RBAC Check")

    try:
        check_prereqs()

        principal_id = args.principal_id or get_signed_in_user_id()

        print_section("Target")
        print_detail(f"Resource group: {_c(WHITE, args.resource_group)}")
        print_detail(f"Cosmos account: {_c(WHITE, args.account_name)}")
        print_detail(f"Role name:      {_c(WHITE, args.role_name)}")
        print_detail(f"Principal ID:   {_c(WHITE, principal_id)}")

        # 1. Role definition
        print_section("Role Definition")
        role = get_role_definition(args.resource_group, args.account_name, args.role_name)
        if not role:
            print_error(f"Role '{args.role_name}' does not exist on account '{args.account_name}'")
            print_detail("Run scripts/setup_cosmos_rbac.py to create it.")
            return 2

        role_id = role["name"]
        print_success(f"Role '{args.role_name}' exists")
        print_detail(f"Role definition ID: {role_id}")

        data_actions = (role.get("permissions") or [{}])[0].get("dataActions", [])
        print_detail(f"DataActions ({len(data_actions)}):")
        for da in data_actions:
            print(f"      - {da}")

        # 2. Assignment
        print_section("Role Assignment")
        assignments = get_assignments(args.resource_group, args.account_name, principal_id, role_id)
        if not assignments:
            print_error(f"Principal {principal_id} does NOT have '{args.role_name}'")
            print_detail("Run scripts/setup_cosmos_rbac.py --principal-id <id> to assign it.")
            return 1

        print_success(f"Principal HAS '{args.role_name}' ({len(assignments)} assignment(s))")
        for a in assignments:
            print_detail(f"- assignmentId:     {a.get('name')}")
            print_detail(f"  scope:            {a.get('scope')}")
            print_detail(f"  roleDefinitionId: {a.get('roleDefinitionId')}")

        print_header("Check passed")
        print()
        print(_c(YELLOW, "  Note: RBAC propagation can take up to 5 minutes after assignment."))
        print(_c(YELLOW, "  For a true end-to-end test, run a data-plane call as the principal."))
        print()
        return 0

    except KeyboardInterrupt:
        print()
        print_error("Interrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
