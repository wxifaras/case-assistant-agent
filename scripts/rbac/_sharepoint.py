"""SharePoint / Microsoft Graph access validation and permission assignment."""

from __future__ import annotations

from typing import Optional

from ._utils import (
    _GRAPH_APP_ID,
    _run_json,
    _run_str,
    print_detail,
    print_error,
    print_section,
    print_skip,
    print_step,
    print_success,
    print_warning,
)


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
        ok, result = _run_str(
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

    ok, consent_result = _run_str(["az", "ad", "app", "permission", "admin-consent", "--id", app_id])
    if ok:
        print_success("Admin consent granted for Microsoft Graph application permissions")
    else:
        print_warning("Could not grant admin consent automatically.")
        if isinstance(consent_result, str) and consent_result:
            print_detail(consent_result)
        print_detail("Ask a Global Admin to run:")
        print_detail(f"az ad app permission admin-consent --id {app_id}")


def setup_sharepoint_delegated_permissions(
    principal_id: str,
    principal_type: str,
    permission_values: list[str],
) -> None:
    """Grant Microsoft Graph *delegated* permissions for SharePoint sync.

    This targets service principals only. It configures delegated permissions on
    the associated app registration and then requests admin consent.
    """
    print_section("SharePoint / Graph Delegated Permissions")

    if principal_type != "ServicePrincipal":
        print_skip("SharePoint delegated permissions (supported only for ServicePrincipal targets)")
        return

    ok, sp = _run_json(["az", "ad", "sp", "show", "--id", principal_id, "-o", "json"])
    if not ok or not isinstance(sp, dict):
        print_error(f"Could not resolve service principal '{principal_id}'")
        if isinstance(sp, str) and sp:
            print_detail(sp)
        return

    app_id = str(sp.get("appId") or "").strip()
    if not app_id:
        print_error("Service principal has no appId; cannot grant Graph delegated permissions.")
        return

    ok, graph_sp = _run_json(["az", "ad", "sp", "show", "--id", _GRAPH_APP_ID, "-o", "json"])
    if not ok or not isinstance(graph_sp, dict):
        print_error("Could not resolve Microsoft Graph service principal.")
        if isinstance(graph_sp, str) and graph_sp:
            print_detail(graph_sp)
        return

    delegated_scopes = graph_sp.get("oauth2PermissionScopes", [])
    if not isinstance(delegated_scopes, list):
        delegated_scopes = []

    scope_ids: list[tuple[str, str]] = []
    for value in permission_values:
        match = next(
            (
                scope
                for scope in delegated_scopes
                if str(scope.get("value") or "").strip().lower() == value.lower() and bool(scope.get("isEnabled", True))
            ),
            None,
        )
        if not isinstance(match, dict):
            print_warning(f"Microsoft Graph delegated permission '{value}' not found — skipping.")
            continue
        scope_id = str(match.get("id") or "").strip()
        if not scope_id:
            print_warning(f"Microsoft Graph delegated permission '{value}' has no scope ID — skipping.")
            continue
        scope_ids.append((value, scope_id))

    if not scope_ids:
        print_warning("No valid Microsoft Graph delegated permissions to assign.")
        return

    for value, scope_id in scope_ids:
        ok, result = _run_str(
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
                f"{scope_id}=Scope",
            ]
        )
        if ok:
            print_success(f"Added Microsoft Graph delegated permission '{value}'")
            continue
        message = str(result)
        if "Permission entry already exists" in message or "already exists" in message.lower():
            print_warning(f"Microsoft Graph delegated permission '{value}' already present")
        else:
            print_error(f"Failed to add Microsoft Graph delegated permission '{value}': {message}")

    ok, consent_result = _run_str(["az", "ad", "app", "permission", "admin-consent", "--id", app_id])
    if ok:
        print_success("Admin consent granted for Microsoft Graph delegated permissions")
    else:
        print_warning("Could not grant admin consent automatically.")
        if isinstance(consent_result, str) and consent_result:
            print_detail(consent_result)
        print_detail("Ask a Global Admin to run:")
        print_detail(f"az ad app permission admin-consent --id {app_id}")
