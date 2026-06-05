"""Search service and Foundry project managed identity RBAC setup."""

from __future__ import annotations

from typing import Optional

from ._utils import (
    WHITE,
    _assign_arm_role,
    _c,
    _run_json,
    print_error,
    print_section,
    print_skip,
    print_step,
    print_success,
    print_warning,
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


def setup_foundry_project_managed_identity(
    resource_group: str,
    project_name: str,
    ai_services_account: Optional[str],
    search_service: Optional[str],
    subscription_id: str = "",
) -> None:
    """Grant the Foundry project's managed identity the roles it needs at agent runtime.

    The KB MCP connection uses authType: ProjectManagedIdentity, so the project MI is
    the identity that calls AI Services and AI Search when the agent invokes the
    knowledge-base tool.

    Roles assigned:
    - Cognitive Services Data Contributor (Preview)  → AI Services account
    - Search Service Contributor                     → AI Services account
    - Search Index Data Contributor                  → AI Search service
    - Search Service Contributor                     → AI Search service
    """
    print_section(f"Foundry Project Managed Identity  [{project_name}]")

    if not ai_services_account:
        print_error("Cannot resolve Foundry project identity: --ai-services-account is required.")
        return
    if not subscription_id:
        print_error("Cannot resolve Foundry project identity: subscription_id is required.")
        return

    project_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{ai_services_account}"
        f"/projects/{project_name}"
    )
    ok, project = _run_json(["az", "resource", "show", "--ids", project_resource_id])
    if not ok or not isinstance(project, dict):
        print_error(f"Foundry project '{project_name}' not found (resource ID: {project_resource_id}).")
        return

    project_principal_id = (project.get("identity") or {}).get("principalId", "")
    if not project_principal_id:
        print_error(
            f"Could not retrieve managed identity principal ID for project '{project_name}'. "
            "Ensure the project has a system-assigned managed identity enabled."
        )
        return
    print_success(f"Foundry project identity principal ID: {_c(WHITE, project_principal_id)}")

    # ── AI Services account roles ──────────────────────────────────────────
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
                "Cognitive Services Data Contributor (Preview)",
                ai_id,
                project_principal_id,
                f"{ai_services_account} (Foundry project MI)",
                assignee_is_object_id=True,
            )
            _assign_arm_role(
                "Search Service Contributor",
                ai_id,
                project_principal_id,
                f"{ai_services_account} (Foundry project MI)",
                assignee_is_object_id=True,
            )
        else:
            print_warning(f"AI Services account '{ai_services_account}' not found — skipping AI Services roles.")
    else:
        print_skip(
            "Cognitive Services Data Contributor / Search Service Contributor on AI Services (no account provided)"
        )

    # ── Search service roles ───────────────────────────────────────────────
    if search_service:
        ok, svc = _run_json(
            ["az", "search", "service", "show", "--name", search_service, "--resource-group", resource_group]
        )
        svc_id = svc.get("id", "") if isinstance(svc, dict) else ""
        if svc_id:
            _assign_arm_role(
                "Search Index Data Contributor",
                svc_id,
                project_principal_id,
                f"{search_service} (Foundry project MI)",
                assignee_is_object_id=True,
            )
            _assign_arm_role(
                "Search Service Contributor",
                svc_id,
                project_principal_id,
                f"{search_service} (Foundry project MI)",
                assignee_is_object_id=True,
            )
        else:
            print_warning(f"Search service '{search_service}' not found — skipping Search roles.")
    else:
        print_skip("Search Index Data Contributor / Search Service Contributor on Search (no service provided)")
