"""Azure Blob Storage RBAC setup."""

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
