#!/usr/bin/env python3
"""Deploy (create/update), list, or delete a Foundry prompt agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Load backend/.env so vars like FOUNDRY_MODEL are available when running this CLI directly.
try:
    from dotenv import load_dotenv  # noqa: E402

    _ENV_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))
    if os.path.isfile(_ENV_PATH):
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass

from app.agents.agent_config import load_agent_yaml  # noqa: E402
from app.agents.agent_manager import AgentManager  # noqa: E402
from app.core.settings import get_settings  # noqa: E402

# --- Foundry IQ knowledge base provisioning DISABLED ---
# from app.ingestion.search.knowledge_base_service import (
#     KnowledgeBaseService,  # noqa: E402
# )
# from app.models.config_options import KnowledgeBaseOptions  # noqa: E402

DEFAULT_YAML_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "backend", "app", "agents", "case_assistant_agent.yaml")
)

_LOG_LEVEL_MAP: dict[str, int] = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "information": logging.INFO,
    "verbose": logging.DEBUG,
}

logger = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=_LOG_LEVEL_MAP.get(log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid Foundry project endpoint URL")


def _resolve_project_resource_id(explicit: str, endpoint: str) -> str:
    """Return the ARM resource id of the Foundry project.

    Priority:
      1. ``explicit`` (from --project-resource-id / FOUNDRY_PROJECT_RESOURCE_ID).
      2. Constructed from AZURE_SUBSCRIPTION_ID + AZURE_RESOURCE_GROUP and the
         account / project parsed out of the Foundry endpoint URL
         (https://<account>.services.ai.azure.com/api/projects/<project>).
      3. Resolve subscription / resource group via the ``az`` CLI by looking up
         the Cognitive Services account whose name was parsed from the endpoint.
    """
    if explicit:
        return explicit if explicit.startswith("/") else f"/{explicit}"

    parsed = urlparse(endpoint)
    host = parsed.netloc or ""
    account = host.split(".", 1)[0] if host else ""
    path_parts = [p for p in parsed.path.split("/") if p]
    project = ""
    if "projects" in path_parts:
        idx = path_parts.index("projects")
        if idx + 1 < len(path_parts):
            project = path_parts[idx + 1]

    if not account or not project:
        raise RuntimeError(
            "Cannot parse account/project from Foundry endpoint; provide --project-resource-id "
            "or set FOUNDRY_PROJECT_RESOURCE_ID."
        )

    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP", "")

    if not subscription_id or not resource_group:
        sub, rg = _lookup_account_via_az(account)
        subscription_id = subscription_id or sub
        resource_group = resource_group or rg

    if not subscription_id or not resource_group:
        raise RuntimeError(
            "Cannot derive Foundry project resource id; provide --project-resource-id or set "
            "FOUNDRY_PROJECT_RESOURCE_ID (or AZURE_SUBSCRIPTION_ID + AZURE_RESOURCE_GROUP, "
            "or ensure 'az' is logged in and can see the account)."
        )

    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}"
    )


def _lookup_account_via_az(account: str) -> tuple[str, str]:
    """Look up (subscription_id, resource_group) for a Cognitive Services account via az CLI.

    Uses the built-in ``az resource list`` (no extra extension required) and pins ``stdin``
    to /dev/null so the call cannot block on an interactive install prompt. Returns
    ("", "") on any failure so callers can surface a clean error message.
    """
    import shutil
    import subprocess

    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        logger.warning("'az' CLI not found on PATH; cannot auto-resolve project resource id.")
        return "", ""

    logger.info("Looking up account '%s' via 'az resource list' ...", account)
    cmd = [
        az,
        "resource",
        "list",
        "--resource-type",
        "Microsoft.CognitiveServices/accounts",
        "--name",
        account,
        "--query",
        "[].{id:id, resourceGroup:resourceGroup}",
        "-o",
        "json",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Failed to invoke 'az resource list': %s", exc)
        return "", ""

    if result.returncode != 0:
        logger.warning(
            "'az resource list' failed (exit %s): %s",
            result.returncode,
            (result.stderr or "").strip(),
        )
        return "", ""

    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse 'az resource list' output: %s", exc)
        return "", ""

    if not payload:
        logger.warning("'az resource list' returned no accounts named '%s'.", account)
        return "", ""

    row = payload[0]
    full_id = str(row.get("id") or "")
    rg = str(row.get("resourceGroup") or "")
    sub = ""
    parts = [p for p in full_id.split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "subscriptions":
        sub = parts[1]
    if sub and rg:
        logger.info("Resolved account '%s' -> sub=%s rg=%s", account, sub, rg)
    return sub, rg


def _write_final_result(output_format: str, payload: dict[str, Any]) -> None:
    if output_format == "json":
        print(f"AGENT_DEPLOYMENT_RESULT: {json.dumps(payload, separators=(',', ':'))}")
        return

    if payload.get("success"):
        logger.info("Agent operation succeeded")
        for key in ("operationType", "agentName", "agentId", "model"):
            if payload.get(key):
                logger.info("  %s: %s", key, payload[key])
    else:
        logger.error("Agent operation failed: %s", payload.get("error", "unknown error"))


# --- Foundry IQ knowledge base provisioning DISABLED ---
# async def _provision_knowledge_base(
#     kb_options: KnowledgeBaseOptions, search_endpoint: str, api_version: str
# ) -> str | None:
#     """Provision the knowledge source + knowledge agent. Returns the KB name."""
#     if not search_endpoint:
#         raise RuntimeError("SEARCH_ENDPOINT is required to provision the knowledge base")
#
#     logger.info(
#         "Provisioning knowledge base '%s' on %s (api-version=%s)",
#         kb_options.name,
#         search_endpoint,
#         api_version,
#     )
#     svc = KnowledgeBaseService(search_endpoint=search_endpoint, api_version=api_version)
#     try:
#         await svc.create_or_update_knowledge_base_async(kb_options)
#     finally:
#         await svc.close()
#     return kb_options.name


_ARM_CONNECTIONS_API_VERSION = "2025-10-01-preview"


def _kb_mcp_endpoint(search_endpoint: str, kb_name: str, api_version: str) -> str:
    base = search_endpoint.rstrip("/")
    return f"{base}/knowledgebases/{kb_name}/mcp?api-version={api_version}"


def _ensure_mcp_kb_connection(
    project_resource_id: str,
    connection_name: str,
    mcp_endpoint: str,
    kb_name: str,
) -> None:
    """Create or update a Foundry RemoteTool project connection that targets the KB MCP endpoint.

    Uses ARM REST (PUT /connections/{name}) with ProjectManagedIdentity auth, per the
    Foundry IQ "Connect a knowledge base to Foundry Agent Service" guide.

    The ``metadata.type == "knowledgeBase_MCP"`` (plus ``knowledgeBaseName``) marker is
    required: Foundry Agent Service only treats a RemoteTool connection as a Foundry IQ
    knowledge base when this metadata is present. Without it the MCP tool is attached to
    the agent but the knowledge base is never actually invoked at runtime.
    """
    credential = DefaultAzureCredential(exclude_environment_credential=True)
    token_provider = get_bearer_token_provider(credential, "https://management.azure.com/.default")
    headers = {
        "Authorization": f"Bearer {token_provider()}",
        "Content-Type": "application/json",
    }
    url = (
        f"https://management.azure.com{project_resource_id}"
        f"/connections/{connection_name}?api-version={_ARM_CONNECTIONS_API_VERSION}"
    )
    body = {
        "name": connection_name,
        "type": "Microsoft.MachineLearningServices/workspaces/connections",
        "properties": {
            "authType": "ProjectManagedIdentity",
            "category": "RemoteTool",
            "target": mcp_endpoint,
            "isSharedToAll": True,
            "audience": "https://search.azure.com/",
            "metadata": {
                "type": "knowledgeBase_MCP",
                "knowledgeBaseName": kb_name,
            },
        },
    }
    logger.info("Provisioning Foundry RemoteTool connection '%s' -> %s", connection_name, mcp_endpoint)
    resp = requests.put(url, headers=headers, json=body, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Failed to create/update project connection '{connection_name}': " f"{resp.status_code} {resp.text}"
        )
    logger.info("Connection '%s' created or updated successfully.", connection_name)


def _patch_mcp_kb_tool(
    tools: list[Any],
    kb_name: str | None,
    search_endpoint: str | None,
    connection_name: str,
    api_version: str,
) -> bool:
    """Fill the placeholder ``server_url`` / ``project_connection_id`` on the MCP KB tool entry.

    Returns True if at least one MCP tool was patched (i.e. the agent uses Foundry IQ).
    ``project_connection_id`` is set to the bare connection name, matching the binding the
    Foundry portal writes for a Foundry IQ knowledge base.
    """
    patched = False
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("type") or "").lower() != "mcp":
            continue
        # Only auto-patch the well-known KB MCP entry. Other MCP tools pass through.
        if str(tool.get("server_label") or "").lower() != "knowledge-base":
            continue
        if not tool.get("server_url"):
            if not (kb_name and search_endpoint):
                raise RuntimeError(
                    "MCP tool 'knowledge-base' has empty server_url but KB provisioning was skipped "
                    "or SEARCH_ENDPOINT is not configured. Remove --skip-knowledge-base or hard-code "
                    "server_url in the YAML."
                )
            tool["server_url"] = _kb_mcp_endpoint(search_endpoint, kb_name, api_version)
            logger.info("Patched MCP tool server_url -> '%s'", tool["server_url"])
        if not tool.get("project_connection_id"):
            tool["project_connection_id"] = connection_name
            logger.info("Patched MCP tool project_connection_id -> '%s'", connection_name)
        patched = True
    return patched



async def _deploy(
    endpoint: str,
    yaml_path: str,
    output_format: str,
    agent_name: str,
    model_name: str,
    skip_knowledge_base: bool,
    knowledge_base_only: bool,
    project_resource_id: str,
    kb_connection_name: str,
) -> None:
    yaml_config = load_agent_yaml(yaml_path)
    final_name = agent_name or yaml_config["name"]
    final_model = model_name or yaml_config["model"]

    settings = get_settings()
    # --- Foundry IQ knowledge base provisioning DISABLED ---
    # kb_options = settings.knowledge_base_options
    search_endpoint = settings.search_service.endpoint
    kb_api_version = settings.knowledge_base.api_version

    kb_name: str | None = None
    # if not skip_knowledge_base:
    #     kb_name = await _provision_knowledge_base(kb_options, search_endpoint, kb_api_version)

    if knowledge_base_only:
        payload = {
            "success": True,
            "operationType": "knowledge_base_only",
            "knowledgeBaseName": kb_name,
            "endpoint": endpoint,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _write_final_result(output_format, payload)
        return

    tools = list(yaml_config["tools"] or [])
    uses_foundry_iq = _patch_mcp_kb_tool(tools, kb_name, search_endpoint, kb_connection_name, kb_api_version)

    if uses_foundry_iq:
        logger.info("Resolving Foundry project resource id ...")
        resolved_project_resource_id = _resolve_project_resource_id(project_resource_id, endpoint)
        logger.info("Using project resource id: %s", resolved_project_resource_id)
        if not (kb_name and search_endpoint):
            raise RuntimeError("Cannot provision Foundry IQ MCP connection without KB name and search endpoint.")
        _ensure_mcp_kb_connection(
            project_resource_id=resolved_project_resource_id,
            connection_name=kb_connection_name,
            mcp_endpoint=_kb_mcp_endpoint(search_endpoint, kb_name, kb_api_version),
            kb_name=kb_name,
        )

    logger.info("Creating or updating prompt agent '%s' (model=%s) ...", final_name, final_model)

    config = {
        "name": final_name,
        "description": yaml_config["description"],
        "model": final_model,
        "instructions": yaml_config["instructions"],
        "tools": tools,
        "temperature": yaml_config["temperature"],
        "top_p": yaml_config["top_p"],
        "tool_choice": yaml_config["tool_choice"],
        "reasoning_effort": yaml_config["reasoning_effort"],
        "metadata": {
            "created_by": "deploy-agent-script",
            "created_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "yaml_version": yaml_config["version"],
            "deployment_method": "python-script",
        },
    }

    manager = AgentManager(project_endpoint=endpoint)
    try:
        agent, operation_type = await manager.ensure_agent(config=config)
        payload = {
            "success": True,
            "agentId": str(getattr(agent, "id", "")),
            "agentName": str(getattr(agent, "name", final_name)),
            "model": str(getattr(agent, "model", final_model)),
            "operationType": operation_type,
            "knowledgeBaseName": kb_name,
            "endpoint": endpoint,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _write_final_result(output_format, payload)
    except Exception as exc:
        _write_final_result(output_format, {"success": False, "error": str(exc)})
        raise
    finally:
        await manager.close()


async def _list(endpoint: str) -> None:
    manager = AgentManager(project_endpoint=endpoint)
    try:
        agents = await manager.list_agents()
        if not agents:
            logger.info("No agents found")
            return
        for item in agents:
            logger.info("%s  name=%s  model=%s", item.id, item.name, item.model or "")
    finally:
        await manager.close()


async def _delete(endpoint: str, agent_name: str, delete_knowledge_base: bool) -> None:
    manager = AgentManager(project_endpoint=endpoint)
    try:
        await manager.delete_agent(agent_name)
        logger.info("Deleted agent: %s", agent_name)
    finally:
        await manager.close()

    if not delete_knowledge_base:
        return

    settings = get_settings()
    kb_options = settings.knowledge_base_options
    search_endpoint = settings.search_service.endpoint
    if not search_endpoint:
        logger.warning("SEARCH_ENDPOINT not configured; skipping KB teardown")
        return

    from app.ingestion.search.knowledge_base_service import KnowledgeBaseService

    svc = KnowledgeBaseService(search_endpoint=search_endpoint, api_version=settings.knowledge_base.api_version)
    try:
        await svc.delete_knowledge_base_async(kb_options.name)
        for source in kb_options.knowledge_sources:
            await svc.delete_knowledge_source_async(source.name)
    finally:
        await svc.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Foundry prompt agent")
    parser.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""))
    parser.add_argument("--output-format", choices=["human", "json"], default="human")
    parser.add_argument("--log-level", choices=list(_LOG_LEVEL_MAP.keys()), default="information")

    sub = parser.add_subparsers(dest="command")

    deploy_parser = sub.add_parser("deploy", help="Create or update prompt agent")
    deploy_parser.add_argument("--yaml-path", default=os.environ.get("AGENT_YAML_PATH", DEFAULT_YAML_PATH))
    deploy_parser.add_argument("--agent-name", default="")
    deploy_parser.add_argument("--model", default=os.environ.get("FOUNDRY_MODEL", ""))
    deploy_parser.add_argument(
        "--skip-knowledge-base",
        action="store_true",
        help="Skip Knowledge Base / Source provisioning",
    )
    deploy_parser.add_argument(
        "--knowledge-base-only",
        action="store_true",
        help="Provision the Knowledge Base / Source but do not register the prompt agent",
    )
    deploy_parser.add_argument(
        "--project-resource-id",
        default=os.environ.get("FOUNDRY_PROJECT_RESOURCE_ID", ""),
        help=(
            "ARM resource id of the Foundry project (required when the agent uses the Foundry IQ "
            "MCP knowledge-base tool, used to create the RemoteTool project connection)."
        ),
    )
    deploy_parser.add_argument(
        "--kb-connection-name",
        default=os.environ.get("FOUNDRY_KB_CONNECTION_NAME", "case-assistant-kb-mcp"),
        help="Friendly name for the Foundry RemoteTool project connection that fronts the KB MCP endpoint.",
    )

    sub.add_parser("list", help="List project agents")

    delete_parser = sub.add_parser("delete", help="Delete an agent by name")
    delete_parser.add_argument("agent_name")
    delete_parser.add_argument(
        "--delete-knowledge-base",
        action="store_true",
        help="Also delete the Knowledge Base and its knowledge sources defined in settings",
    )

    args = parser.parse_args()
    _configure_logging(args.log_level)

    if not args.endpoint:
        parser.error("Foundry project endpoint is required")

    _validate_endpoint(args.endpoint)

    command = args.command or "deploy"
    if command == "deploy":
        asyncio.run(
            _deploy(
                endpoint=args.endpoint,
                yaml_path=getattr(args, "yaml_path", None) or os.environ.get("AGENT_YAML_PATH", DEFAULT_YAML_PATH),
                output_format=args.output_format,
                agent_name=getattr(args, "agent_name", ""),
                model_name=getattr(args, "model", "") or os.environ.get("FOUNDRY_MODEL", ""),
                skip_knowledge_base=getattr(args, "skip_knowledge_base", False),
                knowledge_base_only=getattr(args, "knowledge_base_only", False),
                project_resource_id=getattr(args, "project_resource_id", "")
                or os.environ.get("FOUNDRY_PROJECT_RESOURCE_ID", ""),
                kb_connection_name=getattr(args, "kb_connection_name", "")
                or os.environ.get("FOUNDRY_KB_CONNECTION_NAME", "case-assistant-kb-mcp"),
            )
        )
    elif command == "list":
        asyncio.run(_list(args.endpoint))
    elif command == "delete":
        asyncio.run(
            _delete(
                args.endpoint,
                args.agent_name,
                delete_knowledge_base=getattr(args, "delete_knowledge_base", False),
            )
        )


if __name__ == "__main__":
    main()
