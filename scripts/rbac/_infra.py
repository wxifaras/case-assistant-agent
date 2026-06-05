"""App Configuration, Application Insights, Key Vault, and Service Bus RBAC setup."""

from __future__ import annotations

from typing import Optional

from ._utils import (
    CYAN,
    WHITE,
    YELLOW,
    _assign_arm_role,
    _c,
    _resource_exists,
    _run_json,
    print_detail,
    print_error,
    print_section,
    print_skip,
    print_warning,
)


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
    _assign_arm_role(
        "Log Analytics Reader",
        ai_id,
        principal_id,
        component_name,
        assignee_is_object_id=assignee_is_object_id,
    )

    workspace_id = ai.get("workspaceResourceId", "")
    if workspace_id:
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

    rbac_enabled = kv.get("properties", {}).get("enableRbacAuthorization", False)
    if not rbac_enabled:
        print_warning(f"Key Vault '{vault_name}' does not have RBAC authorization enabled. Enable it with:")
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
