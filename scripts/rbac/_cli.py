"""CLI entry point — argument parsing, orchestration, and summary output."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from ._ai_services import setup_ai_multiservice, setup_ai_services
from ._cosmos import setup_cosmos
from ._infra import (
    setup_app_config,
    setup_app_insights,
    setup_key_vault,
    setup_service_bus,
)
from ._managed_identity import (
    setup_foundry_project_managed_identity,
    setup_search_managed_identity,
)
from ._search import setup_search
from ._sharepoint import (
    setup_sharepoint_access,
    setup_sharepoint_app_permissions,
    setup_sharepoint_delegated_permissions,
)
from ._storage import setup_storage
from ._utils import (
    CYAN,
    WHITE,
    YELLOW,
    _c,
    check_prereqs,
    ensure_login,
    get_current_user_id,
    print_header,
    print_section,
    print_skip,
    print_success,
    print_warning,
    select_subscription,
    set_principal_type,
)


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
        "--foundry-project",
        metavar="PROJECT_NAME",
        help=(
            "Foundry project name (e.g. projDev001). When provided, resolves the project's "
            "system-assigned managed identity and grants it the roles required for the KB MCP "
            "connection at agent runtime: Cognitive Services Data Contributor (Preview) and "
            "Search Service Contributor on the AI Services account, plus Search Index Data "
            "Contributor and Search Service Contributor on the Search service."
        ),
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
        help="SharePoint hostname for Graph validation (used only with --validate-sharepoint-access).",
    )
    parser.add_argument(
        "--sharepoint-site-path",
        metavar="SITE_PATH",
        help="SharePoint site server-relative path for Graph validation (used only with --validate-sharepoint-access).",
    )
    parser.add_argument(
        "--sharepoint-library-name",
        metavar="LIBRARY_NAME",
        help="Optional SharePoint document library name to validate (used only with --validate-sharepoint-access).",
    )
    parser.add_argument(
        "--validate-sharepoint-access",
        action="store_true",
        help="Run optional local-user SharePoint/Graph access validation. This is separate from permission assignment.",
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
        "--grant-sharepoint-delegated-permissions",
        action="store_true",
        help="When targeting --principal-id as ServicePrincipal, grant Microsoft Graph delegated permissions for SharePoint sync token acquisition.",
    )
    parser.add_argument(
        "--sharepoint-delegated-permissions",
        default="Sites.Read.All,Files.Read.All",
        help=(
            "Comma-separated Microsoft Graph delegated permissions to grant "
            "(default: Sites.Read.All,Files.Read.All)."
        ),
    )
    parser.add_argument(
        "--principal-id",
        metavar="OBJECT_ID",
        help="Object (principal) ID of a managed identity / service principal to assign roles to. "
        "When provided, the signed-in user is NOT used; roles are assigned to this principal instead.",
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

    if args.principal_id:
        principal_id = args.principal_id
        assignee_is_object_id = True
        set_principal_type(args.principal_type)
        print_section("Target principal")
        print_success(f"Granting roles to {args.principal_type}: {_c(WHITE, principal_id)}")
    else:
        principal_id = get_current_user_id(args.tenant_id)
        assignee_is_object_id = False

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
    sharepoint_site_hostname: str | None = args.sharepoint_site_hostname
    sharepoint_site_path: str | None = args.sharepoint_site_path
    sharepoint_library_name: str | None = args.sharepoint_library_name
    if args.validate_sharepoint_access and not args.principal_id:
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

    # ── Role assignments ───────────────────────────────────────────────────

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

    foundry_project = _prompt_if_missing(
        getattr(args, "foundry_project", None), "Foundry project name (for KB MCP runtime roles)"
    )
    if foundry_project:
        setup_foundry_project_managed_identity(
            resource_group=resource_group,
            project_name=foundry_project,
            ai_services_account=ai_services_account,
            search_service=search_service,
            subscription_id=subscription_id,
        )
    else:
        print_skip("Foundry Project Managed Identity")

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

    if args.grant_sharepoint_delegated_permissions:
        requested_delegated_permissions = [
            p.strip() for p in (args.sharepoint_delegated_permissions or "").split(",") if p.strip()
        ]
        setup_sharepoint_delegated_permissions(
            principal_id=principal_id,
            principal_type=args.principal_type,
            permission_values=requested_delegated_permissions,
        )
    else:
        print_skip("SharePoint / Graph Delegated Permissions")

    if args.validate_sharepoint_access and not args.principal_id:
        if sharepoint_site_hostname and sharepoint_site_path:
            setup_sharepoint_access(sharepoint_site_hostname, sharepoint_site_path, sharepoint_library_name)
        else:
            print_skip("SharePoint / Graph Access (both hostname and site path are required)")
    else:
        print_skip("SharePoint / Graph Access")

    print_summary(args)
