#!/usr/bin/env python3
"""
Local developer RBAC setup

Logs in to Azure, selects a subscription, and assigns all RBAC roles required
to run the Case Assistant Agent locally with DefaultAzureCredential.

Roles assigned to the signed-in user:

  Service               Role                              Assignment scope
  ──────────────────────────────────────────────────────────────────────────
  Cosmos DB (NoSQL)     CosmosDB-DataPlane-FullAccess (custom) Cosmos account
  Blob Storage          Storage Blob Data Contributor          Storage account
  Azure AI Search       Search Index Data Contributor          Search service
  Azure AI Search       Search Service Contributor             Search service
  Microsoft Foundry     Azure AI Developer                     AI Services account
  Microsoft Foundry     Azure AI User                          AI Services account
  Microsoft Foundry     Cognitive Services OpenAI User         AI Services account
  AI multi-service      Cognitive Services User                AI multi-service account
  App Configuration     App Configuration Data Reader          AppConfig store
  Key Vault             Key Vault Secrets User                 Key Vault
  Application Insights  Log Analytics Reader                   App Insights resource
  Service Bus           Azure Service Bus Data Sender          Service Bus queue
  Service Bus           Azure Service Bus Data Receiver        Service Bus queue

Roles assigned to the Search service managed identity:

  Service               Role                              Assignment scope
  ──────────────────────────────────────────────────────────────────────────
  Blob Storage          Storage Blob Data Reader             Storage account
  Blob Storage          Storage Blob Data Contributor        Storage account
  AI multi-service      Cognitive Services User              AI multi-service account
  Microsoft Foundry     Cognitive Services User              AI Services account
  Microsoft Foundry     Cognitive Services OpenAI User       AI Services account

Usage:
    python scripts/setup_rbac.py

    # Non-interactive (all values supplied up-front):
    python scripts/setup_rbac.py \\
        --tenant-id   <tenant-id> \\
        --subscription <subscription-id-or-name> \\
        --resource-group <rg> \\
        --principal-type User

    # Grant elevated Microsoft Graph app permissions to a service principal
    # for SharePoint sync (requires admin consent privileges):
    python scripts/setup_rbac.py \
        --resource-group <rg> \
        --principal-id <service-principal-object-id> \
        --principal-type ServicePrincipal \
        --grant-sharepoint-app-permissions
        --storage-account <storage-account-name> \\
        --search-service <search-service-name> \\
        --ai-services-account <ai-services-account-name> \\
        --ai-multiservice-account <ai-multiservice-account-name> \\
        --app-config-store <appconfig-store-name> \\
        --key-vault <key-vault-name> \\
        --app-insights <app-insights-component-name> \
        --servicebus-namespace <servicebus-namespace-name> \
        --servicebus-queue <servicebus-queue-name>

    # Grant roles to a different principal (managed identity, service principal,
    # another user, or a security group) instead of the signed-in user. The
    # signed-in user is only used to create the role assignments and must have
    # Owner or User Access Administrator on the target resources.
    #
    # --principal-type accepts: User | ServicePrincipal | Group
    #   - User             → another Entra ID user
    #   - ServicePrincipal → managed identity OR service principal (default)
    #   - Group            → Entra ID security group
    python scripts/setup_rbac.py \\
        --resource-group <rg> \\
        --cosmos-account <cosmos-account-name> \\
        --principal-id <object-id-of-target-principal> \\
        --principal-type User

Any resource flag left out causes that service to be skipped.

Behaviour notes:
  - Re-runs are idempotent: existing role assignments are detected and skipped.
  - The Search service managed-identity setup (indexer pipeline) only runs when
    targeting the signed-in user; it is skipped when --principal-id is provided.
  - When --principal-id is provided, role assignments are created with the
    principal type specified by --principal-type (default: ServicePrincipal,
    which works for managed identities and service principals). Pass
    --principal-type User to target another Entra ID user, or Group for a
    security group.
    - Optional: use --grant-sharepoint-app-permissions to add Microsoft Graph
        application permissions (default: Sites.Read.All, Files.Read.All,
        Group.Read.All, GroupMember.Read.All, User.Read.All) to the target
        service principal app registration and attempt admin consent.
  - If the Azure CLI token is rejected by Conditional Access (CAE,
    TokenCreatedWithOutdatedPolicies, AADSTS50173, AADSTS700082), the script
    triggers an interactive ``az login`` and retries automatically.
"""

import argparse
import json
import re
import subprocess
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Terminal colour helpers
# ---------------------------------------------------------------------------

# Principal type used by az role assignment create --assignee-principal-type
# when assigning by object ID. Set in main() based on --principal-type.
_PRINCIPAL_TYPE = "ServicePrincipal"

# Foundry roles were renamed (Azure AI User -> Foundry User).
# Use role definition IDs to stay stable across rename rollouts.
_FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"

RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
WHITE = "\033[97m"
BOLD = "\033[1m"


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
# Step 1 – Prerequisites
# ---------------------------------------------------------------------------


def check_prereqs() -> None:
    print_section("Prerequisites")
    ok, _ = _run_str(["az", "--version"])
    if not ok:
        print_error("Azure CLI is not installed. Install from https://aka.ms/azure-cli")
        sys.exit(1)
    print_success("Azure CLI found")


# ---------------------------------------------------------------------------
# Step 2 – Login / tenant
# ---------------------------------------------------------------------------


def ensure_login(tenant_id: Optional[str]) -> dict:
    """Return the active account dict, prompting for login when needed."""
    print_section("Authentication")

    ok, account = _run_json(["az", "account", "show"])
    if ok and isinstance(account, dict):
        user = account.get("user", {}).get("name", "<unknown>")
        print_success(f"Already signed in as: {_c(WHITE, user)}")
        return account

    # Not logged in — trigger interactive login
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


# ---------------------------------------------------------------------------
# Step 3 – Subscription selection
# ---------------------------------------------------------------------------


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

    # If a specific subscription was requested, find it
    if subscription_arg:
        for s in active_subs:
            if s["id"] == subscription_arg or s["name"] == subscription_arg:
                _set_subscription(s["id"])
                print_success(f"Using subscription: {s['name']} ({s['id']})")
                return s["id"]
        print_error(f"Subscription '{subscription_arg}' not found or not enabled.")
        sys.exit(1)

    # Single subscription — use it automatically
    if len(active_subs) == 1:
        sub = active_subs[0]
        _set_subscription(sub["id"])
        print_success(f"Using subscription: {sub['name']} ({sub['id']})")
        return sub["id"]

    # Interactive selection
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


# ---------------------------------------------------------------------------
# Step 4 – Resolve current user object ID
# ---------------------------------------------------------------------------


def get_current_user_id(tenant_id: Optional[str] = None) -> str:
    print_section("Signed-in user identity")

    def _try_lookup() -> tuple[bool, str, str]:
        result = _run(["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"])
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()

    ok, uid, err = _try_lookup()

    # Handle CAE / stale-token / outdated-policy challenges by forcing a fresh
    # interactive login. These manifest as exit-code != 0 with messages like
    # "InteractionRequired", "TokenCreatedWithOutdatedPolicies", or
    # "Continuous access evaluation".
    if not ok:
        cae_markers = (
            "InteractionRequired",
            "Continuous access evaluation",
            "TokenCreatedWithOutdatedPolicies",
            "AADSTS50173",  # token revoked, re-auth required
            "AADSTS700082",  # refresh token expired
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

    # Check if already assigned
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
    # When targeting a principal by object ID, ARM cannot determine the principal type
    # from Graph alone — supply it explicitly to avoid silent failures where the API
    # returns success but no assignment is persisted.
    if assignee_is_object_id:
        create_cmd += ["--assignee-principal-type", _PRINCIPAL_TYPE]

    ok, result = _run_json(create_cmd)
    if ok:
        print_success(f"Assigned '{role}' on {resource_label}")
    else:
        print_error(f"Failed to assign '{role}' on {resource_label}: {result}")


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


_COSMOS_CUSTOM_ROLE_NAME = "CosmosDB-DataPlane-FullAccess"
# Cosmos SQL role definitions only accept data-plane actions. Database/container
# creation are CONTROL-plane operations (Microsoft.DocumentDB/databaseAccounts/sqlDatabases/write
# and .../containers/write) and must be granted via ARM RBAC, not here.
_COSMOS_CUSTOM_ROLE_DATA_ACTIONS = [
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


def _ensure_cosmos_role_definition(resource_group: str, account_name: str) -> str:
    """Return the custom role definition ID, creating the role if it does not exist."""
    import os
    import tempfile

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
            f"[?roleName=='{_COSMOS_CUSTOM_ROLE_NAME}']",
        ]
    )
    if ok and isinstance(existing, list) and existing:
        role_id = existing[0]["name"]
        print_warning(f"Cosmos role '{_COSMOS_CUSTOM_ROLE_NAME}' already exists — reusing")
        return role_id

    import json as _json

    role_body = {
        "RoleName": _COSMOS_CUSTOM_ROLE_NAME,
        "Type": "CustomRole",
        "AssignableScopes": ["/"],
        "Permissions": [{"DataActions": _COSMOS_CUSTOM_ROLE_DATA_ACTIONS}],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        _json.dump(role_body, tmp, indent=2)
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
        print_error(f"Failed to create Cosmos role definition: {result}")
        return ""

    role_id = result["name"]
    print_success(f"Created Cosmos role definition '{_COSMOS_CUSTOM_ROLE_NAME}' ({role_id})")
    return role_id


def _assign_cosmos_data_role(
    resource_group: str,
    account_name: str,
    principal_id: str,
    subscription_id: str,
) -> None:
    """Ensure the custom Cosmos DB data-plane role exists and is assigned to the principal."""
    role_id = _ensure_cosmos_role_definition(resource_group, account_name)
    if not role_id:
        return

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
    if ok and isinstance(existing, list) and existing:
        print_warning(f"'{_COSMOS_CUSTOM_ROLE_NAME}' already assigned")
        return

    ok, result = _run_json(
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
            role_id,
        ]
    )
    if ok:
        print_success(f"Assigned '{_COSMOS_CUSTOM_ROLE_NAME}' (data-plane)")
    else:
        print_error(f"Failed to assign Cosmos DB data role: {result}")
        print_detail("Tip: your account may need 'Owner' or 'User Access Administrator' on the Cosmos account.")


# ---------------------------------------------------------------------------
# Step 5 – Per-service role assignments
# ---------------------------------------------------------------------------


def setup_cosmos(
    resource_group: str,
    account_name: str,
    principal_id: str,
    subscription_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    print_section(f"Cosmos DB  [{account_name}]")
    if not _resource_exists(
        ["az", "cosmosdb", "show", "--name", account_name, "--resource-group", resource_group],
        f"Cosmos DB account '{account_name}'",
    ):
        return

    ok, account = _run_json(["az", "cosmosdb", "show", "--name", account_name, "--resource-group", resource_group])
    endpoint = account.get("documentEndpoint", "") if isinstance(account, dict) else ""
    cosmos_id = account.get("id", "") if isinstance(account, dict) else ""

    # Control-plane: 'DocumentDB Account Contributor' — manage databases and containers
    # (required if the principal needs to create SQL databases/containers via ARM).
    if cosmos_id:
        _assign_arm_role(
            "DocumentDB Account Contributor",
            cosmos_id,
            principal_id,
            account_name,
            assignee_is_object_id=assignee_is_object_id,
        )

    # Cosmos data-plane assignments use principalId directly — works for users and MIs alike.
    _assign_cosmos_data_role(resource_group, account_name, principal_id, subscription_id)

    if endpoint:
        print()
        print(_c(YELLOW, "  Add to .env:"))
        print(_c(WHITE, f"  COSMOS_ENDPOINT={endpoint}"))


def setup_search(
    resource_group: str,
    service_name: str,
    principal_id: str,
    subscription_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    print_section(f"Azure AI Search  [{service_name}]")
    if not _resource_exists(
        ["az", "search", "service", "show", "--name", service_name, "--resource-group", resource_group],
        f"Search service '{service_name}'",
    ):
        return

    ok, svc = _run_json(["az", "search", "service", "show", "--name", service_name, "--resource-group", resource_group])
    svc_id = svc.get("id", "") if isinstance(svc, dict) else ""
    endpoint = f"https://{service_name}.search.windows.net"

    # Search Index Data Contributor — read/write index documents (required for indexing and querying via RBAC)
    _assign_arm_role(
        "Search Index Data Contributor",
        svc_id,
        principal_id,
        service_name,
        assignee_is_object_id=assignee_is_object_id,
    )
    # Search Service Contributor — manage indexes, skillsets, data sources, and indexers
    _assign_arm_role(
        "Search Service Contributor",
        svc_id,
        principal_id,
        service_name,
        assignee_is_object_id=assignee_is_object_id,
    )

    print()
    print(_c(YELLOW, "  Add to .env:"))
    print(_c(WHITE, f"  SEARCHSERVICE_ENDPOINT={endpoint}"))


def setup_storage(
    resource_group: str,
    account_name: str,
    principal_id: str,
    subscription_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    print_section(f"Blob Storage  [{account_name}]")
    if not _resource_exists(
        ["az", "storage", "account", "show", "--name", account_name, "--resource-group", resource_group],
        f"Storage account '{account_name}'",
    ):
        return

    ok, sa = _run_json(["az", "storage", "account", "show", "--name", account_name, "--resource-group", resource_group])
    sa_id = sa.get("id", "") if isinstance(sa, dict) else ""
    blob_endpoint = sa.get("primaryEndpoints", {}).get("blob", "") if isinstance(sa, dict) else ""

    _assign_arm_role(
        "Storage Blob Data Contributor",
        sa_id,
        principal_id,
        account_name,
        assignee_is_object_id=assignee_is_object_id,
    )

    if blob_endpoint:
        print()
        print(_c(YELLOW, "  Add to .env:"))
        print(_c(WHITE, f"  BLOBSTORAGE_ACCOUNT_URL={blob_endpoint}"))


def setup_ai_services(
    resource_group: str,
    account_name: str,
    principal_id: str,
    subscription_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    print_section(f"Microsoft Foundry / AI Services  [{account_name}]")
    if not _resource_exists(
        ["az", "cognitiveservices", "account", "show", "--name", account_name, "--resource-group", resource_group],
        f"AI Services account '{account_name}'",
    ):
        return

    ok, ai = _run_json(
        ["az", "cognitiveservices", "account", "show", "--name", account_name, "--resource-group", resource_group]
    )
    ai_id = ai.get("id", "") if isinstance(ai, dict) else ""

    # Azure AI Developer — manage Foundry projects, evaluations, red teaming
    _assign_arm_role(
        "Azure AI Developer",
        ai_id,
        principal_id,
        account_name,
        assignee_is_object_id=assignee_is_object_id,
    )
    # Foundry User (stable role ID) — use Foundry agents and inference endpoints
    _assign_arm_role(
        _FOUNDRY_USER_ROLE_ID,
        ai_id,
        principal_id,
        account_name,
        assignee_is_object_id=assignee_is_object_id,
    )
    # Cognitive Services OpenAI User — call LLM inference endpoints (required for RBAC auth)
    _assign_arm_role(
        "Cognitive Services OpenAI User",
        ai_id,
        principal_id,
        account_name,
        assignee_is_object_id=assignee_is_object_id,
    )

    endpoint = ai.get("properties", {}).get("endpoint", "") if isinstance(ai, dict) else ""
    if endpoint:
        print()
        print(_c(YELLOW, "  Add to .env:"))
        print(_c(WHITE, f"  FOUNDRY_PROJECT_ENDPOINT={endpoint}"))


def setup_ai_multiservice(
    resource_group: str,
    account_name: str,
    principal_id: str,
    subscription_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    print_section(f"AI Multi-Service Account  [{account_name}]")
    if not _resource_exists(
        ["az", "cognitiveservices", "account", "show", "--name", account_name, "--resource-group", resource_group],
        f"AI multi-service account '{account_name}'",
    ):
        return

    ok, ai = _run_json(
        ["az", "cognitiveservices", "account", "show", "--name", account_name, "--resource-group", resource_group]
    )
    ai_id = ai.get("id", "") if isinstance(ai, dict) else ""

    # Cognitive Services User — use Document Intelligence, Language Service, and other multi-service endpoints
    _assign_arm_role(
        "Cognitive Services User",
        ai_id,
        principal_id,
        account_name,
        assignee_is_object_id=assignee_is_object_id,
    )


def setup_search_managed_identity(
    resource_group: str,
    service_name: str,
    storage_account: Optional[str],
    ai_multiservice_account: Optional[str],
    ai_services_account: Optional[str],
    resource_group_storage: Optional[str] = None,
) -> None:
    """Enable system-assigned identity on the Search service and grant it
    the roles it needs to run the indexer skillset pipeline:

    - Storage Blob Data Reader       → read source documents from Blob Storage
    - Storage Blob Data Contributor   → write extracted images to knowledge store (normalized-images container)
    - Cognitive Services User        → call Document Intelligence (multimodal skillset)
    - Cognitive Services User        → authorize the skillset-level Cognitive Services account used by Search
    - Cognitive Services OpenAI User → call Azure OpenAI for embeddings + image verbalization
    """
    print_section(f"Search Service Managed Identity  [{service_name}]")

    # 1. Ensure system-assigned managed identity is enabled
    print_step("Ensuring system-assigned managed identity is enabled on the Search service...")
    ok, svc = _run_json(
        [
            "az",
            "search",
            "service",
            "update",
            "--name",
            service_name,
            "--resource-group",
            resource_group,
            "--identity-type",
            "SystemAssigned",
        ]
    )
    if not ok or not isinstance(svc, dict):
        print_error(f"Failed to enable managed identity on Search service '{service_name}'.")
        return

    search_principal_id = (svc.get("identity") or {}).get("principalId", "")
    if not search_principal_id:
        # Identity may not be reflected in the update response immediately — fall back to a show call
        print_step("principalId not in update response — querying current service state...")
        ok2, svc2 = _run_json(
            ["az", "search", "service", "show", "--name", service_name, "--resource-group", resource_group]
        )
        if ok2 and isinstance(svc2, dict):
            search_principal_id = (svc2.get("identity") or {}).get("principalId", "")
    if not search_principal_id:
        print_error("Could not retrieve the Search service managed identity principal ID.")
        return
    print_success(f"Search service identity principal ID: {_c(WHITE, search_principal_id)}")

    rg_storage = resource_group_storage or resource_group

    # 2. Storage Blob Data Reader — indexer reads source documents
    #    Storage Blob Data Contributor — knowledge store writes extracted images to normalized-images container
    if storage_account:
        ok, sa = _run_json(
            ["az", "storage", "account", "show", "--name", storage_account, "--resource-group", rg_storage]
        )
        sa_id = sa.get("id", "") if isinstance(sa, dict) else ""
        if sa_id:
            _assign_arm_role(
                "Storage Blob Data Reader",
                sa_id,
                search_principal_id,
                f"{storage_account} (search identity)",
                assignee_is_object_id=True,
            )
            _assign_arm_role(
                "Storage Blob Data Contributor",
                sa_id,
                search_principal_id,
                f"{storage_account} (search identity — knowledge store)",
                assignee_is_object_id=True,
            )
        else:
            print_warning(f"Storage account '{storage_account}' not found — skipping storage roles.")
    else:
        print_skip("Storage Blob Data Reader / Contributor (no storage account provided)")

    # 3. Cognitive Services User — Document Intelligence skill
    if ai_multiservice_account:
        ok, ai = _run_json(
            [
                "az",
                "cognitiveservices",
                "account",
                "show",
                "--name",
                ai_multiservice_account,
                "--resource-group",
                resource_group,
            ]
        )
        ai_id = ai.get("id", "") if isinstance(ai, dict) else ""
        if ai_id:
            _assign_arm_role(
                "Cognitive Services User",
                ai_id,
                search_principal_id,
                f"{ai_multiservice_account} (search identity)",
                assignee_is_object_id=True,
            )
        else:
            print_warning(f"AI multi-service account '{ai_multiservice_account}' not found — skipping.")
    else:
        print_skip("Cognitive Services User (no AI multi-service account provided)")

    # 4. Foundry / AI Services roles — embedding + image verbalization skills
    if ai_services_account:
        ok, ai = _run_json(
            [
                "az",
                "cognitiveservices",
                "account",
                "show",
                "--name",
                ai_services_account,
                "--resource-group",
                resource_group,
            ]
        )
        ai_id = ai.get("id", "") if isinstance(ai, dict) else ""
        if ai_id:
            _assign_arm_role(
                "Cognitive Services User",
                ai_id,
                search_principal_id,
                f"{ai_services_account} (search identity)",
                assignee_is_object_id=True,
            )
            _assign_arm_role(
                "Cognitive Services OpenAI User",
                ai_id,
                search_principal_id,
                f"{ai_services_account} (search identity)",
                assignee_is_object_id=True,
            )
        else:
            print_warning(f"AI Services account '{ai_services_account}' not found — skipping.")
    else:
        print_skip("Cognitive Services User / OpenAI User (no AI Services account provided)")


def setup_app_config(
    resource_group: str,
    store_name: str,
    principal_id: str,
    subscription_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    print_section(f"App Configuration  [{store_name}]")
    if not _resource_exists(
        ["az", "appconfig", "show", "--name", store_name, "--resource-group", resource_group],
        f"App Configuration store '{store_name}'",
    ):
        return

    ok, ac = _run_json(["az", "appconfig", "show", "--name", store_name, "--resource-group", resource_group])
    ac_id = ac.get("id", "") if isinstance(ac, dict) else ""
    endpoint = ac.get("endpoint", "") if isinstance(ac, dict) else ""

    _assign_arm_role(
        "App Configuration Data Reader",
        ac_id,
        principal_id,
        store_name,
        assignee_is_object_id=assignee_is_object_id,
    )

    if endpoint:
        print()
        print(_c(YELLOW, "  Add to .env:"))
        print(_c(WHITE, f"  APP_CONFIG_ENDPOINT={endpoint}"))


def setup_app_insights(
    resource_group: str,
    component_name: str,
    principal_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    """Assign 'Log Analytics Reader' so the user/SP can query trace data in App Insights.

    Required for:
    - Viewing traces in the new Foundry portal (Observability > Traces)
    - Cloud trace-based evaluation via azure_ai_traces data source
    """
    print_section(f"Application Insights  [{component_name}]")
    if not _resource_exists(
        [
            "az",
            "monitor",
            "app-insights",
            "component",
            "show",
            "--app",
            component_name,
            "--resource-group",
            resource_group,
        ],
        f"App Insights component '{component_name}'",
    ):
        return

    ok, ai = _run_json(
        [
            "az",
            "monitor",
            "app-insights",
            "component",
            "show",
            "--app",
            component_name,
            "--resource-group",
            resource_group,
        ]
    )
    if not ok or not isinstance(ai, dict):
        print_error(f"Could not retrieve App Insights component '{component_name}'.")
        return

    ai_id = ai.get("id", "")
    # Log Analytics Reader — required to query trace data for Foundry trace evaluation
    _assign_arm_role(
        "Log Analytics Reader",
        ai_id,
        principal_id,
        component_name,
        assignee_is_object_id=assignee_is_object_id,
    )

    workspace_id = ai.get("workspaceResourceId", "")
    if workspace_id:
        # Also assign on the linked Log Analytics workspace
        _assign_arm_role(
            "Log Analytics Reader",
            workspace_id,
            principal_id,
            f"{component_name} (workspace)",
            assignee_is_object_id=assignee_is_object_id,
        )


def setup_key_vault(
    resource_group: str,
    vault_name: str,
    principal_id: str,
    subscription_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    """Assign 'Key Vault Secrets User' so the runtime identity can resolve Key Vault references
    stored in Azure App Configuration. Uses RBAC authorization — no access policies required.
    """
    print_section(f"Key Vault  [{vault_name}]")
    if not _resource_exists(
        ["az", "keyvault", "show", "--name", vault_name, "--resource-group", resource_group],
        f"Key Vault '{vault_name}'",
    ):
        return

    ok, kv = _run_json(["az", "keyvault", "show", "--name", vault_name, "--resource-group", resource_group])
    if not ok or not isinstance(kv, dict):
        print_error(f"Could not retrieve Key Vault '{vault_name}'.")
        return

    kv_id = kv.get("id", "")
    vault_uri = kv.get("properties", {}).get("vaultUri", "")

    # Verify the vault uses RBAC authorization (not vault access policies).
    # RBAC auth is required for role-assignment-based access.
    rbac_enabled = kv.get("properties", {}).get("enableRbacAuthorization", False)
    if not rbac_enabled:
        print_warning(f"Key Vault '{vault_name}' does not have RBAC authorization enabled. " "Enable it with:")
        print_detail(
            f"az keyvault update --name {vault_name} "
            f"--resource-group {resource_group} --enable-rbac-authorization true"
        )
        return

    _assign_arm_role(
        "Key Vault Secrets User",
        kv_id,
        principal_id,
        vault_name,
        assignee_is_object_id=assignee_is_object_id,
    )

    if vault_uri:
        print()
        print(_c(YELLOW, "  Store secrets here and reference them from App Configuration:"))
        print(_c(WHITE, f"    Vault URI: {vault_uri}"))
        print(_c(CYAN, "  In the App Configuration store, add a 'Key Vault reference' value"))
        print(_c(CYAN, "  pointing to each secret. The SDK resolves them automatically at startup."))


def setup_service_bus(
    resource_group: str,
    namespace_name: str,
    queue_name: Optional[str],
    principal_id: str,
    assignee_is_object_id: bool = False,
) -> None:
    """Assign Service Bus data-plane roles for queue send/receive operations."""
    label = f"Service Bus  [{namespace_name}]"
    if queue_name:
        label = f"Service Bus  [{namespace_name}/{queue_name}]"
    print_section(label)

    if not _resource_exists(
        ["az", "servicebus", "namespace", "show", "--name", namespace_name, "--resource-group", resource_group],
        f"Service Bus namespace '{namespace_name}'",
    ):
        return

    ok, namespace = _run_json(
        ["az", "servicebus", "namespace", "show", "--name", namespace_name, "--resource-group", resource_group]
    )
    if not ok or not isinstance(namespace, dict):
        print_error(f"Could not retrieve Service Bus namespace '{namespace_name}'.")
        return

    scope = namespace.get("id", "")
    resource_label = namespace_name

    if queue_name:
        if not _resource_exists(
            [
                "az",
                "servicebus",
                "queue",
                "show",
                "--namespace-name",
                namespace_name,
                "--name",
                queue_name,
                "--resource-group",
                resource_group,
            ],
            f"Service Bus queue '{queue_name}'",
        ):
            return

        ok, queue = _run_json(
            [
                "az",
                "servicebus",
                "queue",
                "show",
                "--namespace-name",
                namespace_name,
                "--name",
                queue_name,
                "--resource-group",
                resource_group,
            ]
        )
        if not ok or not isinstance(queue, dict):
            print_error(f"Could not retrieve Service Bus queue '{queue_name}'.")
            return

        scope = queue.get("id", "")
        resource_label = f"{namespace_name}/{queue_name}"

    _assign_arm_role(
        "Azure Service Bus Data Sender",
        scope,
        principal_id,
        resource_label,
        assignee_is_object_id=assignee_is_object_id,
    )
    _assign_arm_role(
        "Azure Service Bus Data Receiver",
        scope,
        principal_id,
        resource_label,
        assignee_is_object_id=assignee_is_object_id,
    )

    print()
    print(_c(YELLOW, "  Add to .env:"))
    print(_c(WHITE, f"  SERVICEBUS_FQDN={namespace_name}.servicebus.windows.net"))
    if queue_name:
        print(_c(WHITE, f"  SERVICEBUS_QUEUE_NAME={queue_name}"))


def setup_sharepoint_access(
    site_hostname: str,
    site_path: str,
    library_name: Optional[str] = None,
) -> None:
    """Validate Microsoft Graph/SharePoint read access for the signed-in local user.

    SharePoint sync uses Graph and user/site permissions rather than Azure ARM RBAC.
    This check verifies that the current local identity can resolve the target site
    and (optionally) read the target document library.
    """
    print_section(f"SharePoint / Graph Access  [{site_hostname}{site_path}]")

    normalized_site_path = site_path if site_path.startswith("/") else f"/{site_path}"
    encoded_site_path = normalized_site_path.replace(" ", "%20")

    ok, site = _run_json(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--url",
            f"https://graph.microsoft.com/v1.0/sites/{site_hostname}:{encoded_site_path}",
        ]
    )
    if not ok or not isinstance(site, dict):
        print_warning("Could not resolve SharePoint site via Microsoft Graph.")
        if isinstance(site, str) and site:
            print_detail(site)
        print_detail("Ensure your local account has access to the SharePoint site and can call Graph.")
        print_detail("If running with an app identity, grant admin-consented Graph app permissions:")
        print_detail("- Sites.Read.All")
        print_detail("- Files.Read.All")
        print_detail("- Group.Read.All")
        print_detail("- GroupMember.Read.All")
        print_detail("- User.Read.All")
        return

    site_id = site.get("id", "")
    web_url = site.get("webUrl", "")
    print_success("SharePoint site is accessible through Graph")
    if site_id:
        print_detail(f"Site ID: {site_id}")
    if web_url:
        print_detail(f"Web URL: {web_url}")

    if not library_name:
        print_step("Library check skipped (no --sharepoint-library-name provided)")
        return

    ok, drives = _run_json(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--url",
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives",
        ]
    )
    if not ok or not isinstance(drives, dict):
        print_warning("Could not enumerate site drives (document libraries).")
        if isinstance(drives, str) and drives:
            print_detail(drives)
        return

    entries = drives.get("value", [])
    if not isinstance(entries, list):
        entries = []

    match = next((d for d in entries if (d.get("name") or "").lower() == library_name.lower()), None)
    if match:
        print_success(f"Library '{library_name}' is accessible")
    else:
        print_warning(f"Library '{library_name}' was not found or is not accessible for this account.")
        if entries:
            visible = ", ".join(sorted([(d.get("name") or "<unnamed>") for d in entries]))
            print_detail(f"Visible libraries: {visible}")


def setup_sharepoint_app_permissions(
    principal_id: str,
    principal_type: str,
    permission_values: list[str],
) -> None:
    """Grant Microsoft Graph *application* permissions for SharePoint sync.

    This targets service principals only. It configures app permissions on the
    associated app registration and then requests admin consent.
    """
    print_section("SharePoint / Graph App Permissions")

    if principal_type != "ServicePrincipal":
        print_skip("SharePoint app permissions (supported only for ServicePrincipal targets)")
        return

    ok, sp = _run_json(["az", "ad", "sp", "show", "--id", principal_id, "-o", "json"])
    if not ok or not isinstance(sp, dict):
        print_error(f"Could not resolve service principal '{principal_id}'")
        if isinstance(sp, str) and sp:
            print_detail(sp)
        return

    app_id = str(sp.get("appId") or "").strip()
    if not app_id:
        print_error("Service principal has no appId; cannot grant Graph app permissions.")
        return

    ok, graph_sp = _run_json(["az", "ad", "sp", "show", "--id", _GRAPH_APP_ID, "-o", "json"])
    if not ok or not isinstance(graph_sp, dict):
        print_error("Could not resolve Microsoft Graph service principal.")
        if isinstance(graph_sp, str) and graph_sp:
            print_detail(graph_sp)
        return

    app_roles = graph_sp.get("appRoles", [])
    if not isinstance(app_roles, list):
        app_roles = []

    role_ids: list[tuple[str, str]] = []
    for value in permission_values:
        match = next(
            (
                role
                for role in app_roles
                if str(role.get("value") or "").strip().lower() == value.lower()
                and "Application" in (role.get("allowedMemberTypes") or [])
                and bool(role.get("isEnabled", True))
            ),
            None,
        )
        if not isinstance(match, dict):
            print_warning(f"Microsoft Graph application permission '{value}' not found — skipping.")
            continue
        role_id = str(match.get("id") or "").strip()
        if not role_id:
            print_warning(f"Microsoft Graph application permission '{value}' has no role ID — skipping.")
            continue
        role_ids.append((value, role_id))

    if not role_ids:
        print_warning("No valid Microsoft Graph application permissions to assign.")
        return

    for value, role_id in role_ids:
        ok, result = _run_json(
            [
                "az",
                "ad",
                "app",
                "permission",
                "add",
                "--id",
                app_id,
                "--api",
                _GRAPH_APP_ID,
                "--api-permissions",
                f"{role_id}=Role",
            ]
        )
        if ok:
            print_success(f"Added Microsoft Graph app permission '{value}'")
            continue

        message = str(result)
        if "Permission entry already exists" in message or "already exists" in message.lower():
            print_warning(f"Microsoft Graph app permission '{value}' already present")
        else:
            print_error(f"Failed to add Microsoft Graph app permission '{value}': {message}")

    ok, consent_result = _run_json(["az", "ad", "app", "permission", "admin-consent", "--id", app_id])
    if ok:
        print_success("Admin consent granted for Microsoft Graph application permissions")
    else:
        print_warning("Could not grant admin consent automatically.")
        if isinstance(consent_result, str) and consent_result:
            print_detail(consent_result)
        print_detail("Ask a Global Admin to run:")
        print_detail(f"az ad app permission admin-consent --id {app_id}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(args: argparse.Namespace) -> None:
    print_header("Setup complete")
    print()
    print(_c(CYAN, "  Role propagation can take up to 5 minutes."))
    print()
    print(_c(WHITE, "  Next steps:"))
    print("    1. Copy backend/.env.example  →  backend/.env")
    print("    2. Fill in the endpoint values printed above")
    print("    3. (Optional) Store secrets in Key Vault and add Key Vault references in App Configuration")
    print("    4. In VS Code, run the following tasks in order:")
    print("         › Create venv (Python 3.12)")
    print("         › Install Python Dependencies (dev)")
    print("         › FastAPI: Run Dev Server")
    print()
    print(_c(CYAN, "  Use 'az login' again at any time to refresh credentials."))
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup_rbac.py",
        description=(
            "Assign Azure RBAC roles for local development to the currently signed-in user. "
            "Omit a resource flag to skip that service."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tenant-id", "-t", metavar="TENANT_ID", help="Entra ID tenant ID (used if login is needed).")
    parser.add_argument(
        "--subscription", "-s", metavar="SUBSCRIPTION", help="Subscription name or ID (interactive if omitted)."
    )
    parser.add_argument(
        "--resource-group", "-g", metavar="RESOURCE_GROUP", help="Resource group that contains all services."
    )
    parser.add_argument("--cosmos-account", metavar="ACCOUNT_NAME", help="Cosmos DB account name.")
    parser.add_argument("--storage-account", metavar="ACCOUNT_NAME", help="Storage account name.")
    parser.add_argument("--search-service", metavar="SERVICE_NAME", help="Azure AI Search service name.")
    parser.add_argument(
        "--ai-services-account",
        metavar="ACCOUNT_NAME",
        help="Azure AI Services / Cognitive Services account name (Microsoft Foundry).",
    )
    parser.add_argument(
        "--ai-multiservice-account",
        metavar="ACCOUNT_NAME",
        help="Azure AI multi-service account name (for Document Intelligence, Language Service, etc.).",
    )
    parser.add_argument(
        "--app-config-store",
        metavar="STORE_NAME",
        help="App Configuration store name (optional — skipped if not supplied).",
    )
    parser.add_argument(
        "--key-vault",
        metavar="VAULT_NAME",
        help="Key Vault name (optional — skipped if not supplied). "
        "Assigns 'Key Vault Secrets User' role; vault must have RBAC authorization enabled.",
    )
    parser.add_argument(
        "--app-insights",
        metavar="COMPONENT_NAME",
        help="Application Insights component name (optional). "
        "Assigns 'Log Analytics Reader' for trace queries and cloud trace evaluation.",
    )
    parser.add_argument(
        "--servicebus-namespace",
        metavar="NAMESPACE_NAME",
        help="Service Bus namespace name (optional). Assigns sender/receiver roles.",
    )
    parser.add_argument(
        "--servicebus-queue",
        metavar="QUEUE_NAME",
        help="Service Bus queue name (optional). If set, scopes roles to this queue.",
    )
    parser.add_argument(
        "--sharepoint-site-hostname",
        metavar="HOSTNAME",
        help="SharePoint hostname for Graph validation (for example: contoso.sharepoint.com).",
    )
    parser.add_argument(
        "--sharepoint-site-path",
        metavar="SITE_PATH",
        help="SharePoint site server-relative path for Graph validation (for example: /sites/MySite).",
    )
    parser.add_argument(
        "--sharepoint-library-name",
        metavar="LIBRARY_NAME",
        help="Optional SharePoint document library display name to validate (for example: Documents).",
    )
    parser.add_argument(
        "--grant-sharepoint-app-permissions",
        action="store_true",
        help="When targeting --principal-id as ServicePrincipal, grant elevated Microsoft Graph application permissions for SharePoint sync.",
    )
    parser.add_argument(
        "--sharepoint-app-permissions",
        default="Sites.Read.All,Files.Read.All,Group.Read.All,GroupMember.Read.All,User.Read.All",
        help=(
            "Comma-separated Microsoft Graph application permissions to grant "
            "(default: Sites.Read.All,Files.Read.All,Group.Read.All,GroupMember.Read.All,User.Read.All)."
        ),
    )
    parser.add_argument(
        "--principal-id",
        metavar="OBJECT_ID",
        help="Object (principal) ID of a managed identity / service principal to assign roles to. "
        "When provided, the signed-in user is NOT used; roles are assigned to this principal instead. "
        "Useful for granting the Foundry project's managed identity access to all dependencies.",
    )
    parser.add_argument(
        "--principal-type",
        choices=["User", "ServicePrincipal", "Group"],
        default="ServicePrincipal",
        help="Type of principal referenced by --principal-id (default: ServicePrincipal). "
        "Use 'User' to grant roles to another Entra ID user, or 'Group' for a security group.",
    )
    return parser


def _prompt_if_missing(current: Optional[str], prompt: str) -> Optional[str]:
    """Return current if set, otherwise prompt the user (empty input = skip)."""
    if current:
        return current
    value = input(_c(CYAN, f"  {prompt} (leave blank to skip): ")).strip()
    return value or None


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    print_header("Workflow Automation — Local Dev RBAC Setup")

    check_prereqs()
    ensure_login(args.tenant_id)
    subscription_id = select_subscription(args.subscription)

    # Determine the principal to grant roles to.
    # When --principal-id is provided, target that managed identity / service principal instead
    # of the signed-in user. Otherwise, use the signed-in user (default local-dev behaviour).
    if args.principal_id:
        principal_id = args.principal_id
        assignee_is_object_id = True
        global _PRINCIPAL_TYPE
        _PRINCIPAL_TYPE = args.principal_type
        print_section("Target principal")
        print_success(f"Granting roles to {args.principal_type}: {_c(WHITE, principal_id)}")
    else:
        principal_id = get_current_user_id(args.tenant_id)
        assignee_is_object_id = False

    # Resolve resource group — required if any resource is being configured
    print_section("Resources to configure")
    print(_c(WHITE, "  Leave blank to skip a service.\n"))

    resource_group = _prompt_if_missing(args.resource_group, "Resource group name")
    if not resource_group:
        print_warning("No resource group provided — nothing to configure.")
        sys.exit(0)

    cosmos_account = _prompt_if_missing(args.cosmos_account, "Cosmos DB account name")
    storage_account = _prompt_if_missing(args.storage_account, "Storage account name")
    search_service = _prompt_if_missing(args.search_service, "Azure AI Search service name")
    ai_services_account = _prompt_if_missing(args.ai_services_account, "AI Services account name (Foundry)")
    ai_multiservice_account = _prompt_if_missing(
        args.ai_multiservice_account, "AI multi-service account name (Document Intelligence / Language Service)"
    )
    app_config_store = (
        _prompt_if_missing(args.app_config_store, "App Configuration store name")
        if args.app_config_store is not None
        else None
    )
    key_vault = _prompt_if_missing(args.key_vault, "Key Vault name")
    app_insights = _prompt_if_missing(args.app_insights, "Application Insights component name")
    servicebus_namespace = _prompt_if_missing(args.servicebus_namespace, "Service Bus namespace name")
    servicebus_queue = _prompt_if_missing(
        args.servicebus_queue,
        "Service Bus queue name (optional; leave blank to scope at namespace)",
    )
    sharepoint_site_hostname = _prompt_if_missing(
        args.sharepoint_site_hostname, "SharePoint site hostname (for Graph access validation)"
    )
    sharepoint_site_path = _prompt_if_missing(
        args.sharepoint_site_path, "SharePoint site path (for Graph access validation, e.g. /sites/MySite)"
    )
    sharepoint_library_name = _prompt_if_missing(
        args.sharepoint_library_name,
        "SharePoint library name (optional Graph validation, e.g. Documents)",
    )

    # Assign roles
    if cosmos_account:
        setup_cosmos(resource_group, cosmos_account, principal_id, subscription_id, assignee_is_object_id)
    else:
        print_skip("Cosmos DB")

    if storage_account:
        setup_storage(resource_group, storage_account, principal_id, subscription_id, assignee_is_object_id)
    else:
        print_skip("Blob Storage")

    if search_service:
        setup_search(resource_group, search_service, principal_id, subscription_id, assignee_is_object_id)
    else:
        print_skip("Azure AI Search")

    if ai_services_account:
        setup_ai_services(resource_group, ai_services_account, principal_id, subscription_id, assignee_is_object_id)
    else:
        print_skip("Microsoft Foundry / AI Services")

    if ai_multiservice_account:
        setup_ai_multiservice(
            resource_group, ai_multiservice_account, principal_id, subscription_id, assignee_is_object_id
        )
    else:
        print_skip("AI Multi-Service Account")

    # Search service managed identity — grants the indexer pipeline access to downstream resources.
    # Only run this when targeting the signed-in user; when --principal-id is set we are explicitly
    # targeting a different identity and should not also reconfigure the Search service identity.
    if search_service and not args.principal_id:
        setup_search_managed_identity(
            resource_group=resource_group,
            service_name=search_service,
            storage_account=storage_account,
            ai_multiservice_account=ai_multiservice_account,
            ai_services_account=ai_services_account,
        )
    elif not search_service:
        print_skip("Search Service Managed Identity")

    if app_config_store:
        setup_app_config(resource_group, app_config_store, principal_id, subscription_id, assignee_is_object_id)
    else:
        print_skip("App Configuration")

    if key_vault:
        setup_key_vault(resource_group, key_vault, principal_id, subscription_id, assignee_is_object_id)
    else:
        print_skip("Key Vault")

    if app_insights:
        setup_app_insights(resource_group, app_insights, principal_id, assignee_is_object_id)
    else:
        print_skip("Application Insights")

    if servicebus_namespace:
        setup_service_bus(
            resource_group=resource_group,
            namespace_name=servicebus_namespace,
            queue_name=servicebus_queue,
            principal_id=principal_id,
            assignee_is_object_id=assignee_is_object_id,
        )
    else:
        print_skip("Service Bus")

    if args.grant_sharepoint_app_permissions:
        requested_permissions = [p.strip() for p in (args.sharepoint_app_permissions or "").split(",") if p.strip()]
        setup_sharepoint_app_permissions(
            principal_id=principal_id,
            principal_type=args.principal_type,
            permission_values=requested_permissions,
        )
    else:
        print_skip("SharePoint / Graph App Permissions")

    # SharePoint/Graph access check applies to local user workflow only.
    # When --principal-id is provided, the target identity may not be interactive,
    # so Graph validation of user/library access is skipped.
    if sharepoint_site_hostname and sharepoint_site_path and not args.principal_id:
        setup_sharepoint_access(sharepoint_site_hostname, sharepoint_site_path, sharepoint_library_name)
    elif not sharepoint_site_hostname and not sharepoint_site_path:
        print_skip("SharePoint / Graph Access")
    elif not args.principal_id:
        print_skip("SharePoint / Graph Access (both hostname and site path are required)")

    print_summary(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(_c(YELLOW, "\n\n  Interrupted."))
        sys.exit(1)
