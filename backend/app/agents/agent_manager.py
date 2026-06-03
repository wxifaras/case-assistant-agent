"""Foundry prompt-agent lifecycle helpers (create/update/list/delete)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import (
    AISearchIndexResource,
    AzureAISearchTool,
    AzureAISearchToolResource,
    MCPTool,
    PromptAgentDefinition,
)
from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential

from app.agents.agent_config import CaseAssistantAgentConfig


@dataclass(slots=True)
class AgentRecord:
    id: str
    name: str
    model: str | None = None


def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Drop YAML-only keys (``type``) and apply defaults before constructing an SDK tool model."""
    normalized = {k: v for k, v in tool.items() if k != "type"}
    if "require_approval" not in normalized:
        normalized["require_approval"] = "never"
    return normalized


class AgentManager:
    """Manages prompt-agent lifecycle against a Foundry project endpoint."""

    def __init__(self, project_endpoint: str) -> None:
        self._endpoint = project_endpoint
        self._credential: DefaultAzureCredential | None = None
        self._client: AIProjectClient | None = None

    def _ensure_client(self) -> AIProjectClient:
        if self._client is None:
            self._credential = DefaultAzureCredential()
            self._client = AIProjectClient(endpoint=self._endpoint, credential=self._credential)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()
        if self._credential:
            await self._credential.close()

    async def _get_agent_or_none(self, name: str):
        client = self._ensure_client()
        try:
            return await client.agents.get(name)
        except ResourceNotFoundError:
            return None

    async def _resolve_connection_id(self, connection_name: str) -> str:
        client = self._ensure_client()
        conn = await client.connections.get(connection_name)
        conn_id = getattr(conn, "id", None)
        if not conn_id:
            raise RuntimeError(f"Foundry connection '{connection_name}' has no id; cannot bind Azure AI Search tool")
        return str(conn_id)

    async def _build_azure_ai_search_tool(self, tool: dict[str, Any]) -> Any:
        """Build an Azure AI Search tool entry for the prompt agent.

        Returns one of two shapes:

        * Typed :class:`AzureAISearchTool` — when bound to one or more indexes
          directly (``index_name`` provided).
        * Raw ``dict`` payload — when bound to a knowledge base
          (``knowledge_base_name`` provided). The typed SDK does not yet
          expose a KB-bound variant, so we emit the wire format directly.
        """
        cfg = tool.get("azure_ai_search") or {}

        # ----- Knowledge-base-backed variant -----
        kb_name = cfg.get("knowledge_base_name")
        if kb_name:
            project_connection_id = cfg.get("project_connection_id") or cfg.get("index_connection_id")
            if not project_connection_id:
                connection_name = cfg.get("connection_name")
                if not connection_name:
                    raise ValueError(
                        "azure_ai_search (knowledge_base) tool requires 'project_connection_id' or 'connection_name'"
                    )
                project_connection_id = await self._resolve_connection_id(connection_name)
            return {
                "type": "azure_ai_search",
                "azure_ai_search": {
                    "indexes": [
                        {
                            "index_connection_id": project_connection_id,
                            "knowledge_base_name": kb_name,
                        }
                    ],
                },
            }

        # ----- Index-direct variant (existing behaviour) -----
        indexes_cfg = cfg.get("indexes")
        if not indexes_cfg:
            # Single-index shorthand at the top of azure_ai_search block.
            indexes_cfg = [cfg]

        index_resources: list[AISearchIndexResource] = []
        for idx in indexes_cfg:
            project_connection_id = idx.get("project_connection_id") or idx.get("index_connection_id")
            if not project_connection_id:
                connection_name = idx.get("connection_name")
                if not connection_name:
                    raise ValueError("azure_ai_search tool requires 'project_connection_id' or 'connection_name'")
                project_connection_id = await self._resolve_connection_id(connection_name)

            index_name = idx.get("index_name")
            if not index_name:
                raise ValueError("azure_ai_search tool requires 'index_name' or 'knowledge_base_name'")

            resource_kwargs: dict[str, Any] = {
                "project_connection_id": project_connection_id,
                "index_name": index_name,
            }
            if "query_type" in idx and idx["query_type"]:
                resource_kwargs["query_type"] = idx["query_type"]
            if "top_k" in idx and idx["top_k"] is not None:
                resource_kwargs["top_k"] = int(idx["top_k"])
            if "filter" in idx and idx["filter"]:
                resource_kwargs["filter"] = idx["filter"]

            index_resources.append(AISearchIndexResource(**resource_kwargs))

        return AzureAISearchTool(azure_ai_search=AzureAISearchToolResource(indexes=index_resources))

    async def _build_tools(self, raw_tools: list[Any]) -> list[Any]:
        tools: list[Any] = []
        for t in raw_tools:
            if not isinstance(t, dict):
                tools.append(t)
                continue
            tool_type = str(t.get("type") or "").lower()
            if tool_type == "azure_ai_search":
                tools.append(await self._build_azure_ai_search_tool(t))
            else:
                tools.append(MCPTool(**_normalize_tool(t)))
        return tools

    async def ensure_agent(self, config: dict[str, Any] | None = None):
        client = self._ensure_client()
        cfg = config or CaseAssistantAgentConfig.get_agent_config()

        tools = await self._build_tools(cfg.get("tools") or [])

        definition = PromptAgentDefinition(
            model=cfg["model"],
            instructions=cfg.get("instructions", ""),
            temperature=cfg.get("temperature", 1.0),
            top_p=cfg.get("top_p", 1.0),
            tools=tools,
        )

        existing = await self._get_agent_or_none(cfg["name"])
        operation_type = "updated" if existing is not None else "created"

        # In azure-ai-projects 2.x, both create and update use create_version.
        # Each call creates a new version of the agent identified by agent_name.
        result = await client.agents.create_version(
            agent_name=cfg["name"],
            definition=definition,
            description=cfg.get("description", ""),
            metadata=cfg.get("metadata") or {},
        )
        return result, operation_type

    async def delete_agent(self, agent_name: str) -> None:
        client = self._ensure_client()
        try:
            await client.agents.delete(agent_name)
        except ResourceNotFoundError:
            return

    async def list_agents(self) -> list[AgentRecord]:
        client = self._ensure_client()
        agents: list[AgentRecord] = []
        async for agent in client.agents.list():
            model: str | None = None
            versions = getattr(agent, "versions", None)
            latest = getattr(versions, "latest", None) if versions is not None else None
            definition = getattr(latest, "definition", None) if latest is not None else None
            if definition is not None:
                model = getattr(definition, "model", None)
            agents.append(
                AgentRecord(
                    id=str(getattr(agent, "id", "")),
                    name=str(getattr(agent, "name", "")),
                    model=model,
                )
            )
        return agents
