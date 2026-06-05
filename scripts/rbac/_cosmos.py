"""Cosmos DB RBAC setup — custom data-plane role definition and assignment."""

from __future__ import annotations

import json as _json
import os
import tempfile
from typing import Optional

from ._utils import (
    WHITE,
    YELLOW,
    _assign_arm_role,
    _c,
    _resource_exists,
    _run_json,
    print_detail,
    print_error,
    print_section,
    print_success,
    print_warning,
)

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

    if cosmos_id:
        _assign_arm_role(
            "DocumentDB Account Contributor",
            cosmos_id,
            principal_id,
            account_name,
            assignee_is_object_id=assignee_is_object_id,
        )

    _assign_cosmos_data_role(resource_group, account_name, principal_id, subscription_id)

    if endpoint:
        print()
        print(_c(YELLOW, "  Add to .env:"))
        print(_c(WHITE, f"  COSMOS_ENDPOINT={endpoint}"))
