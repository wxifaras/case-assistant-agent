"""Microsoft Foundry / AI Services and AI multi-service RBAC setup."""

from __future__ import annotations

from ._utils import (
    _FOUNDRY_USER_ROLE_ID,
    WHITE,
    YELLOW,
    _assign_arm_role,
    _c,
    _resource_exists,
    _run_json,
    print_section,
)


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
