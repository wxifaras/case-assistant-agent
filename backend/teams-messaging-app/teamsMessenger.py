"""Proactive Microsoft Teams messaging service.

Teams does not allow sending a proactive message to a user by email or UPN
— a stored ``conversation_id`` per user is required. This service:

- captures user conversation references (keyed by AAD object id) whenever
  the application calls :meth:`register_user`,
- exposes :meth:`send_to_user`, which returns a structured
  :class:`SendResult` so callers can distinguish *user not reachable* from
  *send failed*.

Reaching users who have never spoken to the bot requires installing it for
them via the Microsoft Graph teamsAppInstallation API; that is outside the
scope of this service.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Protocol

from microsoft_teams.api import MessageActivityInput
from microsoft_teams.apps import App

logger = logging.getLogger(__name__)


class SendResult(Enum):
    SENT = "sent"
    USER_UNKNOWN = "user_unknown"
    SEND_FAILED = "send_failed"


class ITeamsMessenger(Protocol):
    def register_user(
        self, aad_object_id: str | None, conversation_id: str
    ) -> None: ...

    async def send_to_user(
        self, aad_object_id: str, text: str
    ) -> SendResult: ...


class TeamsMessenger:
    """In-memory user-conversation registry + proactive send wrapper.

    In-memory only — replace ``_user_conversations`` with a Cosmos / Redis
    backing store for multi-replica deployments.
    """

    def __init__(self, app: App) -> None:
        self._app = app
        self._user_conversations: dict[str, str] = {}

    def register_user(
        self, aad_object_id: str | None, conversation_id: str
    ) -> None:
        if not aad_object_id:
            return
        if self._user_conversations.get(aad_object_id) != conversation_id:
            self._user_conversations[aad_object_id] = conversation_id
            logger.debug(
                "Registered conversation for user %s", aad_object_id
            )

    async def send_to_user(
        self, aad_object_id: str, text: str
    ) -> SendResult:
        conv_id = self._user_conversations.get(aad_object_id)
        if not conv_id:
            return SendResult.USER_UNKNOWN
        try:
            await self._app.send(conv_id, MessageActivityInput(text=text))
            return SendResult.SENT
        except Exception:
            logger.exception(
                "Failed to send proactive message to %s", aad_object_id
            )
            return SendResult.SEND_FAILED