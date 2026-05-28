"""Foundry prompt-agent runtime invocation service."""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from typing import Any

from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

from app.core.logger import Logger
from app.models.config_options import FoundryAgentOptions


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


class IFoundryService(ABC):
    """Interface for runtime prompt-agent invocation."""

    @abstractmethod
    async def query_async(self, *, agent_name: str, query: str) -> dict[str, Any]:
        """Invoke the configured Foundry prompt agent and return a normalized result payload."""
        raise NotImplementedError


class FoundryService(IFoundryService):
    """Best-effort Prompt Agent invocation wrapper for azure-ai-projects."""

    def __init__(self, options: FoundryAgentOptions, logger: Logger) -> None:
        self._options = options
        self._logger = logger
        self._endpoint = options.project_endpoint or ""
        self._timeout_seconds = options.timeout_seconds
        self._credential: DefaultAzureCredential | None = None
        self._client: AIProjectClient | None = None

    def _ensure_client(self) -> AIProjectClient:
        if not self._endpoint:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required when Foundry prompt-agent mode is enabled")
        if self._client is None:
            self._credential = DefaultAzureCredential()
            self._client = AIProjectClient(endpoint=self._endpoint, credential=self._credential)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()
        if self._credential:
            await self._credential.close()

    async def _create_thread(self):
        client = self._ensure_client()
        method = getattr(client.agents, "create_thread", None)
        if method is None:
            raise RuntimeError("Installed azure-ai-projects SDK does not expose agents.create_thread().")
        return await _maybe_await(method())

    async def _create_message(self, thread_id: str, content: str):
        client = self._ensure_client()
        method = getattr(client.agents, "create_message", None)
        if method is None:
            raise RuntimeError("Installed azure-ai-projects SDK does not expose agents.create_message().")
        return await _maybe_await(method(thread_id=thread_id, role="user", content=content))

    async def _create_run(self, thread_id: str, agent_name: str):
        client = self._ensure_client()
        method = getattr(client.agents, "create_run", None)
        if method is None:
            raise RuntimeError("Installed azure-ai-projects SDK does not expose agents.create_run().")

        try:
            return await _maybe_await(method(thread_id=thread_id, agent_name=agent_name))
        except TypeError:
            return await _maybe_await(method(thread_id=thread_id, assistant_id=agent_name))

    async def _wait_for_run(self, thread_id: str, run_id: str):
        client = self._ensure_client()
        get_run = getattr(client.agents, "get_run", None)
        if get_run is None:
            raise RuntimeError("Installed azure-ai-projects SDK does not expose agents.get_run().")

        deadline = asyncio.get_running_loop().time() + self._timeout_seconds
        while True:
            run = await _maybe_await(get_run(thread_id=thread_id, run_id=run_id))
            status = str(getattr(run, "status", "")).lower()
            if status in {"completed", "succeeded"}:
                return
            if status in {"failed", "cancelled", "expired"}:
                raise RuntimeError(f"Foundry run failed with status={status}")
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("Foundry run timed out")
            await asyncio.sleep(1.5)

    async def _extract_assistant_text(self, thread_id: str) -> str:
        client = self._ensure_client()
        list_messages = getattr(client.agents, "list_messages", None)
        if list_messages is None:
            raise RuntimeError("Installed azure-ai-projects SDK does not expose agents.list_messages().")

        messages_obj = await _maybe_await(list_messages(thread_id=thread_id))

        # SDK may return an async iterator or page object.
        messages = []
        if hasattr(messages_obj, "__aiter__"):
            async for item in messages_obj:
                messages.append(item)
        elif isinstance(messages_obj, list):
            messages = messages_obj
        elif hasattr(messages_obj, "data"):
            messages = list(messages_obj.data or [])

        for msg in reversed(messages):
            if str(getattr(msg, "role", "")).lower() != "assistant":
                continue
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    text = getattr(part, "text", None)
                    if text and hasattr(text, "value"):
                        text_parts.append(str(text.value))
                    elif isinstance(part, dict):
                        value = part.get("text", {}).get("value") if isinstance(part.get("text"), dict) else None
                        if value:
                            text_parts.append(str(value))
                if text_parts:
                    return "\n".join(text_parts)
        return ""

    async def query_async(self, *, agent_name: str, query: str) -> dict[str, Any]:
        thread = await self._create_thread()
        thread_id = str(getattr(thread, "id", ""))
        if not thread_id:
            raise RuntimeError("Foundry thread creation returned no thread id")

        await self._create_message(thread_id=thread_id, content=query)
        run = await self._create_run(thread_id=thread_id, agent_name=agent_name)
        run_id = str(getattr(run, "id", ""))
        if not run_id:
            raise RuntimeError("Foundry run creation returned no run id")

        await self._wait_for_run(thread_id=thread_id, run_id=run_id)
        answer = (await self._extract_assistant_text(thread_id=thread_id)).strip()

        payload = {
            "answer": answer,
            "citations": [],
            "document_count": 0,
            "metadata": {"thread_id": thread_id, "run_id": run_id},
        }
        self._logger.debug(f"Foundry prompt-agent invocation completed: {payload['metadata']}")
        return payload
