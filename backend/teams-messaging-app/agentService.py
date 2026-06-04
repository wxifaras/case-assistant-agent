"""Microsoft Foundry (V2) agent service.

Encapsulates the OpenAI Responses API exposed through
:class:`AIProjectClient`. Two operations:

- :meth:`FoundryAgentService.ask_oneshot` — single-turn call (used by the
  broadcast orchestrator, one prompt per SharePoint site).
- :meth:`FoundryAgentService.ask_in_chat` — multi-turn call; binds each
  caller-supplied ``chat_key`` to a Foundry conversation so history is
  preserved per chat.

The underlying OpenAI client is synchronous; calls are dispatched through
``asyncio.to_thread`` so the Teams event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

logger = logging.getLogger(__name__)

EMPTY_REPLY = "(no response from agent)"


class IAgentService(Protocol):
    async def ask_oneshot(self, prompt: str) -> str: ...
    async def ask_in_chat(self, chat_key: str, prompt: str) -> str: ...
    async def close(self) -> None: ...


class FoundryAgentService:
    """Foundry V2 agent client driven through the OpenAI Responses protocol."""

    def __init__(self, *, project_endpoint: str, agent_name: str) -> None:
        self._project_client = AIProjectClient(
            endpoint=project_endpoint,
            credential=AzureCliCredential(),
        )
        self._openai = self._project_client.get_openai_client()
        self._agent_reference = {
            "name": agent_name,
            "type": "agent_reference",
        }
        # chat_key (caller-supplied) -> Foundry conversation id
        self._conversations: dict[str, str] = {}

    async def ask_oneshot(self, prompt: str) -> str:
        return await asyncio.to_thread(self._responses_create, prompt, None)

    async def ask_in_chat(self, chat_key: str, prompt: str) -> str:
        conv_id = self._conversations.get(chat_key)
        if not conv_id:
            conv_id = await asyncio.to_thread(self._create_conversation)
            self._conversations[chat_key] = conv_id
            logger.info(
                "Bound chat %s to Foundry conversation %s", chat_key, conv_id
            )
        return await asyncio.to_thread(self._responses_create, prompt, conv_id)

    async def close(self) -> None:
        try:
            self._project_client.close()
        except Exception:
            logger.exception("Failed to close AIProjectClient")

    # ---- sync internals dispatched via asyncio.to_thread ---- #

    def _create_conversation(self) -> str:
        return self._openai.conversations.create().id

    def _responses_create(
        self, prompt: str, conversation_id: str | None
    ) -> str:
        if conversation_id:
            response = self._openai.responses.create(
                conversation=conversation_id,
                extra_body={"agent_reference": self._agent_reference},
                input=prompt,
            )
        else:
            response = self._openai.responses.create(
                extra_body={"agent_reference": self._agent_reference},
                input=prompt,
            )
        return response.output_text or EMPTY_REPLY