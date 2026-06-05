"""Shared constants, terminal colour helpers, Azure CLI wrappers, and RBAC assignment primitives."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Principal type used by az role assignment create --assignee-principal-type
# when assigning by object ID. Set via set_principal_type() in _cli.main().
_PRINCIPAL_TYPE = "ServicePrincipal"

# Foundry roles were renamed (Azure AI User -> Foundry User).
# Use role definition IDs to stay stable across rename rollouts.
_FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"

# ---------------------------------------------------------------------------
# Terminal colours
# ---------------------------------------------------------------------------

RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
WHITE = "\033[97m"
BOLD = "\033[1m"


def set_principal_type(value: str) -> None:
    """Mutate the module-level _PRINCIPAL_TYPE used by _assign_arm_role."""
    global _PRINCIPAL_TYPE
    _PRINCIPAL_TYPE = value


def _c(colour: str, text: str) -> str:
    if sys.stdout.isatty():
        return f"{colour}{text}{RESET}"
    return text


def print_header(msg: str) -> None:
    width = max(len(msg) + 4, 66)
    print(_c(CYAN, "\n" + "═" * width))
    print(_c(CYAN + BOLD, f"  {msg}"))
    print(_c(CYAN, "═" * width))


def print_section(msg: str) -> None:
    print(_c(CYAN, f"\n─── {msg} ───"))


def print_step(msg: str) -> None:
    print(_c(CYAN, f"  › {msg}"))


def print_success(msg: str) -> None:
    print(_c(GREEN, f"  ✓ {msg}"))


def print_warning(msg: str) -> None:
    print(_c(YELLOW, f"  ⚠ {msg}"))


def print_error(msg: str) -> None:
    print(_c(RED, f"  ✗ {msg}"), file=sys.stderr)


def print_detail(msg: str) -> None:
    print(f"      {msg}")


def print_skip(msg: str) -> None:
    print(_c(YELLOW, f"  ⊘ Skipped: {msg}"))


# ---------------------------------------------------------------------------
# Azure CLI helpers
# ---------------------------------------------------------------------------


def _run(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=capture,
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
# Prerequisites / Auth
# ---------------------------------------------------------------------------


def check_prereqs() -> None:
    print_section("Prerequisites")
    ok, _ = _run_str(["az", "--version"])
    if not ok:
        print_error("Azure CLI is not installed. Install from https://aka.ms/azure-cli")
        sys.exit(1)
    print_success("Azure CLI found")


def ensure_login(tenant_id: Optional[str]) -> dict:
    """Return the active account dict, prompting for login when needed."""
    print_section("Authentication")

    ok, account = _run_json(["az", "account", "show"])
    if ok and isinstance(account, dict):
        user = account.get("user", {}).get("name", "<unknown>")
        print_success(f"Already signed in as: {_c(WHITE, user)}")
        return account

    print_step("Not signed in — launching az login...")
    login_cmd = ["az", "login"]
    if tenant_id:
        login_cmd += ["--tenant", tenant_id]
    result = _run(login_cmd, capture=False)
    if result.returncode != 0:
        print_error("Login failed.")
        sys.exit(1)

    ok, account = _run_json(["az", "account", "show"])
    if not ok or not isinstance(account, dict):
        print_error("Could not retrieve account after login.")
        sys.exit(1)

    user = account.get("user", {}).get("name", "<unknown>")
    print_success(f"Signed in as: {_c(WHITE, user)}")
    return account


def select_subscription(subscription_arg: Optional[str]) -> str:
    """Return the subscription ID to use, prompting interactively if needed."""
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
                _set_subscription(s["id"])
                print_success(f"Using subscription: {s['name']} ({s['id']})")
                return s["id"]
        print_error(f"Subscription '{subscription_arg}' not found or not enabled.")
        sys.exit(1)

    if len(active_subs) == 1:
        sub = active_subs[0]
        _set_subscription(sub["id"])
        print_success(f"Using subscription: {sub['name']} ({sub['id']})")
        return sub["id"]

    print()
    print(_c(WHITE, "  Available subscriptions:"))
    for i, s in enumerate(active_subs, 1):
        print(f"    [{i}] {s['name']}")
        print_detail(s["id"])

    while True:
        try:
            choice = input(_c(CYAN, "\n  Select subscription number: ")).strip()
            idx = int(choice) - 1
            if 0 <= idx < len(active_subs):
                sub = active_subs[idx]
                _set_subscription(sub["id"])
                print_success(f"Using subscription: {sub['name']} ({sub['id']})")
                return sub["id"]
        except (ValueError, KeyboardInterrupt):
            pass
        print_warning("Invalid selection, try again.")


def _set_subscription(sub_id: str) -> None:
    ok, _ = _run_str(["az", "account", "set", "--subscription", sub_id])
    if not ok:
        print_error(f"Failed to set subscription {sub_id}")
        sys.exit(1)


def get_current_user_id(tenant_id: Optional[str] = None) -> str:
    print_section("Signed-in user identity")

    def _try_lookup() -> tuple[bool, str, str]:
        result = _run(["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"])
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()

    ok, uid, err = _try_lookup()

    if not ok:
        cae_markers = (
            "InteractionRequired",
            "Continuous access evaluation",
            "TokenCreatedWithOutdatedPolicies",
            "AADSTS50173",
            "AADSTS700082",
        )
        if any(m in err for m in cae_markers):
            print_step("Token rejected by Conditional Access — refreshing credentials via interactive login...")
            login_cmd = ["az", "login"]
            if tenant_id:
                login_cmd += ["--tenant", tenant_id]
            login = _run(login_cmd, capture=False)
            if login.returncode != 0:
                print_error("Interactive login failed.")
                sys.exit(1)
            ok, uid, err = _try_lookup()

    if not ok or not uid:
        if err:
            print_detail(err)
        print_error(
            "Could not retrieve signed-in user object ID. Are you logged in as a user (not a service principal)?"
        )
        sys.exit(1)
    print_success(f"User object ID: {_c(WHITE, uid)}")
    return uid


# ---------------------------------------------------------------------------
# RBAC assignment helpers
# ---------------------------------------------------------------------------


def _resource_exists(check_cmd: list[str], resource_label: str) -> bool:
    ok, _ = _run_json(check_cmd)
    if not ok:
        print_warning(f"{resource_label} not found or not accessible — skipping.")
        return False
    return True


def _is_guid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value))


def _resolve_role_for_assignment(role: str) -> str:
    """Resolve a role name to role definition ID to handle rename rollouts safely."""
    if _is_guid(role):
        return role
    ok, definitions = _run_json(["az", "role", "definition", "list", "--name", role, "-o", "json"])
    if ok and isinstance(definitions, list) and definitions:
        role_id = definitions[0].get("name")
        if isinstance(role_id, str) and _is_guid(role_id):
            return role_id
    return role


def _assign_arm_role(
    role: str,
    scope: str,
    principal_id: str,
    resource_label: str,
    assignee_is_object_id: bool = False,
) -> None:
    """Assign a standard Azure ARM RBAC role, idempotent.

    Set assignee_is_object_id=True for service principals and managed identities
    to use --assignee-object-id, which bypasses Microsoft Graph resolution and
    avoids "principal not found" errors that can occur with --assignee.
    """
    assignee_flag = "--assignee-object-id" if assignee_is_object_id else "--assignee"
    role_for_assignment = _resolve_role_for_assignment(role)

    ok, existing = _run_json(
        [
            "az",
            "role",
            "assignment",
            "list",
            assignee_flag,
            principal_id,
            "--role",
            role_for_assignment,
            "--scope",
            scope,
            "--query",
            "[].id",
            "-o",
            "json",
        ]
    )
    if ok and isinstance(existing, list) and existing:
        print_warning(f"'{role}' already assigned on {resource_label}")
        return

    create_cmd = [
        "az",
        "role",
        "assignment",
        "create",
        assignee_flag,
        principal_id,
        "--role",
        role_for_assignment,
        "--scope",
        scope,
    ]
    if assignee_is_object_id:
        create_cmd += ["--assignee-principal-type", _PRINCIPAL_TYPE]

    ok, result = _run_json(create_cmd)
    if ok:
        print_success(f"Assigned '{role}' on {resource_label}")
    else:
        print_error(f"Failed to assign '{role}' on {resource_label}: {result}")
