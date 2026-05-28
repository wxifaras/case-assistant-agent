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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Load backend/.env so vars like FOUNDRY_MODEL are available when running this CLI directly.
try:
    from dotenv import load_dotenv  # noqa: E402

    _ENV_PATH = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
    )
    if os.path.isfile(_ENV_PATH):
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass

from app.agents.agent_config import load_agent_yaml  # noqa: E402
from app.agents.agent_manager import AgentManager  # noqa: E402

DEFAULT_YAML_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__), "..", "backend", "app", "agents", "case_assistant_agent.yaml"
    )
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


async def _deploy(endpoint: str, yaml_path: str, output_format: str, agent_name: str, model_name: str) -> None:
    yaml_config = load_agent_yaml(yaml_path)
    final_name = agent_name or yaml_config["name"]
    final_model = model_name or yaml_config["model"]

    config = {
        "name": final_name,
        "description": yaml_config["description"],
        "model": final_model,
        "instructions": yaml_config["instructions"],
        "tools": yaml_config["tools"],
        "temperature": yaml_config["temperature"],
        "top_p": yaml_config["top_p"],
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


async def _delete(endpoint: str, agent_name: str) -> None:
    manager = AgentManager(project_endpoint=endpoint)
    try:
        await manager.delete_agent(agent_name)
        logger.info("Deleted agent: %s", agent_name)
    finally:
        await manager.close()


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

    sub.add_parser("list", help="List project agents")

    delete_parser = sub.add_parser("delete", help="Delete an agent by name")
    delete_parser.add_argument("agent_name")

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
            )
        )
    elif command == "list":
        asyncio.run(_list(args.endpoint))
    elif command == "delete":
        asyncio.run(_delete(args.endpoint, args.agent_name))


if __name__ == "__main__":
    main()
