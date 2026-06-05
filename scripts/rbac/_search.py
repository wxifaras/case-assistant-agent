"""Azure AI Search RBAC setup."""

from __future__ import annotations

from ._utils import (
    WHITE,
    YELLOW,
    _assign_arm_role,
    _c,
    _resource_exists,
    _run_json,
    print_section,
)


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
