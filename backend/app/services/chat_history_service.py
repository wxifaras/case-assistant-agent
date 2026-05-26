"""Cosmos DB chat history service for manual session management.

Key points:
- Manages chat history using session_id + user_id pattern
- Uses Hierarchical Partition Key (HPK): [user_id, session_id]
- Provides methods needed by ChatService for agentic RAG workflows
- Uses async Cosmos client (azure.cosmos.aio) to avoid blocking

Schema (Cosmos item) matches ChatHistoryItem:
  id: str                    # unique = f"{user_id}_{session_id}_{message_id}"
  user_id: str               # partition key level 1
  session_id: str            # partition key level 2
  timestamp: str (ISO 8601)  # stored as string for reliable ordering
  serialized_message: str    # JSON with message details
  message_text: Optional[str]
  message_id: str
  role: str

Container requirements:
- Partition key: Hierarchical [/user_id, /session_id]
- You can optionally enable TTL
"""

from __future__ import annotations

import json
import re
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Literal

from agent_framework import Message
from azure.cosmos.exceptions import CosmosHttpResponseError

from app.core.logger import Logger
from app.models.chat import ChatHistoryItem
from app.repositories.cosmos_repository import CosmosRepository


class IChatHistoryService(ABC):
    """Interface for chat history service operations."""

    @abstractmethod
    async def get_user_chat_history(
        self,
        session_id: str,
        user_id: str,
        max_messages: int | None = None,
    ) -> list[Message]:
        """Retrieve chat history with sanitized text (for LLM context).

        Returns messages with citations removed from text. Use this when passing
        chat history to the LLM for generating responses to avoid cross-turn leakage.

        Args:
            session_id: The session identifier
            user_id: The user identifier
            max_messages: Optional limit on number of messages to return

        Returns:
            List of Message with sanitized text in chronological order (oldest -> newest)
        """
        pass

    @abstractmethod
    async def get_user_chat_history_with_citations(
        self,
        session_id: str,
        user_id: str,
        max_messages: int | None = None,
    ) -> list[Message]:
        """Retrieve chat history with full text including citations.

        Returns messages with original text including citation markers and metadata.

        Args:
            session_id: The session identifier
            user_id: The user identifier
            max_messages: Optional limit on number of messages to return

        Returns:
            List of Message with full text and citations in chronological order (oldest -> newest)
        """
        pass

    @abstractmethod
    async def add_user_chat_message(
        self,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a message to chat history.

        Args:
            session_id: The session identifier
            user_id: The user identifier
            role: Message role (user, assistant, system)
            content: Message content
            metadata: Optional metadata to include in serialized_message
        """
        pass

    @abstractmethod
    async def list_user_chat_sessions(
        self,
        user_id: str,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """List all chat sessions for a user.

        Args:
            user_id: The user identifier
            max_results: Optional limit on number of sessions to return

        Returns:
            List of session dictionaries with session_id and metadata
        """
        pass

    @abstractmethod
    async def delete_user_chat_session(
        self,
        session_id: str,
        user_id: str,
    ) -> None:
        """Delete all messages in a chat session.

        Args:
            session_id: The session identifier
            user_id: The user identifier
        """
        pass

    @abstractmethod
    async def clear_user_chat_history(
        self,
        user_id: str,
    ) -> int:
        """Delete all chat history for a user across all sessions.

        Args:
            user_id: The user identifier

        Returns:
            Number of messages deleted
        """
        pass


class ChatHistoryService(IChatHistoryService):
    """
    Cosmos DB chat history service for manual session management.

    Provides methods needed by ChatService for agentic RAG workflows.
    Uses Hierarchical Partition Key (HPK): [user_id, session_id] for better scalability.
    """

    # Pre-compiled regex patterns for efficient citation stripping.
    _NUMERIC_CITATION_PATTERN = re.compile(r"\[\d+\]")
    _CONTENT_ID_CITATION_PATTERN = re.compile(r"\{[^}]+\}")
    _MULTI_SPACE_PATTERN = re.compile(r" {2,}")
    _SPACE_NEWLINE_PATTERN = re.compile(r" ?\n ?")  # Handles both space before and after newline.
    _SPACE_BEFORE_PUNCT_PATTERN = re.compile(r" ([.,])")  # Space before period or comma.
    _MULTI_NEWLINE_PATTERN = re.compile(r"\n{3,}")

    # Role-string → role-literal mapping used when deserializing stored messages.
    # Defined as a class constant so it is built once rather than rebuilt on
    # every call to _payload_to_chat_message.
    _ROLE_MAP: dict[str, Literal["system", "user", "assistant", "tool"]] = {
        "user": "user",
        "assistant": "assistant",
        "system": "system",
    }

    def __init__(
        self,
        repo: CosmosRepository,
        logger: Logger | None = None,
    ):
        self.logger = logger or Logger()
        self._repo = repo

    def _make_partition_key(self, user_id: str, session_id: str) -> list[str]:
        """Create HPK partition key value: [user_id, session_id]."""
        return [user_id, session_id]

    @staticmethod
    def is_not_found_error(ex: Exception) -> bool:
        """Check if exception is a Cosmos DB 404 Not Found error."""
        return isinstance(ex, CosmosHttpResponseError) and getattr(ex, "status_code", None) == 404

    @classmethod
    def sanitize_message_text(cls, text: str) -> str:
        """Sanitize message text by removing citation patterns before saving to conversation history.

        Removes both inline numeric citations [1], [2], etc. and content ID citations
        {Content ID} to prevent citation leakage across conversation turns.

        Args:
            text: Text that may contain citation patterns

        Returns:
            Text with all citation patterns removed and whitespace cleaned up
        """
        # Remove citations using pre-compiled patterns
        result = cls._NUMERIC_CITATION_PATTERN.sub("", text)
        result = cls._CONTENT_ID_CITATION_PATTERN.sub("", result)

        # Clean up whitespace issues using pre-compiled patterns
        result = cls._MULTI_SPACE_PATTERN.sub(" ", result)  # Multiple spaces → single space
        result = cls._SPACE_NEWLINE_PATTERN.sub("\n", result)  # Space before/after newline → just newline
        result = cls._SPACE_BEFORE_PUNCT_PATTERN.sub(r"\1", result)  # Space before punctuation → just punctuation
        result = cls._MULTI_NEWLINE_PATTERN.sub("\n\n", result)  # Multiple newlines → max 2

        return result.strip()

    def _role_to_str(self, role: Any) -> str:
        """Convert Role enum or string to string."""
        if hasattr(role, "value"):
            return str(role.value)
        return str(role)

    def _payload_to_chat_message(
        self,
        payload: dict[str, Any],
        text_override: str | None = None,
    ) -> Message:
        """Convert a serialized payload dict back to a ``Message``.

        Args:
            payload: Deserialized ``serialized_message`` data from Cosmos.
            text_override: When provided, replaces the ``text`` field in the
                payload (used to return pre-sanitized text from
                ``message_text`` instead of the raw payload text).

        Returns:
            Message populated from the payload.
        """
        role_str = (payload.get("role") or "user").lower()

        # Use the class-level constant to avoid rebuilding the dict each call.
        role: Literal["system", "user", "assistant", "tool"] = self._ROLE_MAP.get(role_str, "user")

        # text_override takes priority (sanitised path); fall back to payload text.
        text = text_override if text_override is not None else (payload.get("text", "") or "")

        return Message(
            role=role,
            contents=[text],
            message_id=payload.get("id"),
        )

    async def _fetch_items(
        self,
        session_id: str,
        user_id: str,
        max_messages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch raw Cosmos items for a session, ordered oldest-first.

        Shared by :meth:`get_conversation_history` and
        :meth:`get_conversation_history_with_citations` to avoid duplicating
        the query, pagination, and error-handling logic.

        Args:
            session_id: The session identifier.
            user_id: The user identifier.
            max_messages: When set, return only the *most recent* N messages.

        Returns:
            List of raw Cosmos item dicts, or an empty list on query failure.
        """
        partition_key = self._make_partition_key(user_id, session_id)
        query = (
            "SELECT * FROM c WHERE c.doc_type = @t "
            "ORDER BY c.timestamp ASC"
        )
        parameters: list[dict[str, Any]] = [{"name": "@t", "value": "chat_message"}]

        items: list[dict[str, Any]] = []
        try:
            async for it in self._repo.query_items(
                query,
                parameters=parameters,
                partition_key=partition_key,
            ):
                items.append(it)
        except CosmosHttpResponseError as e:
            self.logger.error(f"[ChatHistoryService] query_items failed: {e}")
            return []

        # Slice to the most-recent N messages after fetching so Cosmos
        # ORDER BY is preserved without a separate TOP + OFFSET clause.
        if max_messages is not None and max_messages > 0 and len(items) > max_messages:
            items = items[-max_messages:]

        return items

    async def get_user_chat_history(
        self,
        session_id: str,
        user_id: str,
        max_messages: int | None = None,
    ) -> list[Message]:
        """
        Retrieve chat history with sanitized text (for LLM context).

        Returns messages with citations removed from text. Use this when passing
        chat history to the LLM for generating responses.

        Args:
            session_id: The session identifier.
            user_id: The user identifier.
            max_messages: Optional limit on number of messages to return.

        Returns:
            List of Message with sanitized text in chronological order (oldest -> newest).
        """
        items = await self._fetch_items(session_id, user_id, max_messages)

        messages: list[Message] = []
        for it in items:
            try:
                payload = json.loads(it["serialized_message"])
            except Exception:
                payload = {
                    "id": it.get("message_id"),
                    "role": it.get("role", "user"),
                }
            # Use the pre-sanitised message_text field to avoid citation leakage.
            sanitized_text = it.get("message_text", "") or ""
            messages.append(self._payload_to_chat_message(payload, text_override=sanitized_text))

        return messages

    async def get_user_chat_history_with_citations(
        self,
        session_id: str,
        user_id: str,
        max_messages: int | None = None,
    ) -> list[Message]:
        """
        Retrieve chat history with full text including citations (for UI display).

        Returns messages with original text including citation markers and metadata.
        Use this when displaying chat history in the UI.

        Args:
            session_id: The session identifier.
            user_id: The user identifier.
            max_messages: Optional limit on number of messages to return.

        Returns:
            List of Message with full text and citations in chronological order (oldest -> newest).
        """
        items = await self._fetch_items(session_id, user_id, max_messages)

        messages: list[Message] = []
        for it in items:
            try:
                payload = json.loads(it["serialized_message"])
            except Exception:
                payload = {
                    "id": it.get("message_id"),
                    "role": it.get("role", "user"),
                    "text": it.get("message_text", "") or "",
                }
            messages.append(self._payload_to_chat_message(payload))

        return messages

    async def add_user_chat_message(
        self,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Add a message to user chat history.

        Args:
            session_id: The session identifier
            user_id: The user identifier
            role: Message role (user, assistant, system)
            content: Message content (citations will be automatically stripped)
            metadata: Optional metadata to include in serialized_message
        """
        message_id = uuid.uuid4().hex
        now = datetime.now(UTC)

        # Sanitize message text by removing all citations before saving to prevent cross-turn citation leakage
        message_text = self.sanitize_message_text(content)

        # Build the serialized message payload
        payload = {
            "id": message_id,
            "role": role,
            "text": message_text,
        }
        if metadata:
            payload.update(metadata)

        item = ChatHistoryItem(
            id=f"{user_id}_{session_id}_{message_id}",
            user_id=user_id,
            session_id=session_id,
            timestamp=now,
            serialized_message=json.dumps(payload, ensure_ascii=False),
            message_text=message_text,
            message_id=message_id,
            role=role,
        )

        # Convert to dict and format timestamp
        doc = item.model_dump()
        doc["timestamp"] = item.timestamp.isoformat()

        try:
            await self._repo.upsert_item(doc)
        except CosmosHttpResponseError as e:
            self.logger.error(f"[ChatHistoryService] upsert_item failed: {e}")
            raise

    async def list_user_chat_sessions(
        self,
        user_id: str,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        List all chat sessions for a user.

        Args:
            user_id: The user identifier
            max_results: Optional limit on number of sessions to return

        Returns:
            List of session dictionaries with session_id and metadata
        """
        # Query distinct session_ids for this user
        # This is a cross-partition query because we need all sessions (different second-level partition keys)
        query = (
            "SELECT DISTINCT c.session_id FROM c "
            "WHERE c.user_id = @user_id AND c.doc_type = @t"
        )
        parameters: list[dict[str, Any]] = [
            {"name": "@user_id", "value": user_id},
            {"name": "@t", "value": "chat_message"},
        ]

        session_ids: list[str] = []
        try:
            async for it in self._repo.query_items(
                query,
                parameters=parameters,
            ):
                if isinstance(it, str):
                    session_id = it
                else:
                    session_id = it.get("session_id")

                if session_id:
                    session_ids.append(session_id)
        except CosmosHttpResponseError as e:
            self.logger.error(f"[ChatHistoryService] list_user_sessions failed: {e}")
            return []

        # Apply max_results if specified
        if max_results is not None and max_results > 0:
            session_ids = session_ids[:max_results]

        return [{"session_id": sid} for sid in session_ids]

    async def delete_user_chat_session(
        self,
        session_id: str,
        user_id: str,
    ) -> None:
        """
        Delete all messages in a user chat session.

        Args:
            session_id: The session identifier
            user_id: The user identifier
        """
        partition_key = self._make_partition_key(user_id, session_id)

        query = "SELECT c.id FROM c WHERE c.doc_type = @t"
        parameters: list[dict[str, Any]] = [{"name": "@t", "value": "chat_message"}]

        try:
            # Get all message IDs
            message_ids = []
            async for it in self._repo.query_items(
                query,
                parameters=parameters,
                partition_key=partition_key,
            ):
                message_ids.append(it["id"])

            # Delete each message
            for msg_id in message_ids:
                await self._repo.delete_item(
                    item_id=msg_id,
                    partition_key=partition_key,
                )

            self.logger.info(
                f"[ChatHistoryService] Deleted {len(message_ids)} messages from session {session_id} for user {user_id}"
            )
        except CosmosHttpResponseError as e:
            self.logger.error(f"[ChatHistoryService] delete_session failed: {e}")
            raise

    async def clear_user_chat_history(
        self,
        user_id: str,
    ) -> int:
        """
        Delete all chat history for a user across all sessions.

        Args:
            user_id: The user identifier

        Returns:
            Number of messages deleted
        """
        # With HPK, we can query by user_id prefix efficiently
        query = (
            "SELECT c.id, c.user_id, c.session_id FROM c "
            "WHERE c.user_id = @user_id AND c.doc_type = @t"
        )
        parameters: list[dict[str, Any]] = [
            {"name": "@user_id", "value": user_id},
            {"name": "@t", "value": "chat_message"},
        ]

        deleted_count = 0
        try:
            # Collect all items to delete
            items_to_delete: list[tuple[str, list[str]]] = []
            async for it in self._repo.query_items(
                query,
                parameters=parameters,
            ):
                item_id = it.get("id")
                u_id = it.get("user_id")
                s_id = it.get("session_id")
                if item_id and u_id and s_id:
                    items_to_delete.append((item_id, [u_id, s_id]))

            # Delete each item using HPK partition key
            for item_id, pk in items_to_delete:
                await self._repo.delete_item(
                    item_id=item_id,
                    partition_key=pk,
                )
                deleted_count += 1

            self.logger.info(f"[ChatHistoryService] Deleted {deleted_count} messages for user {user_id}")
        except CosmosHttpResponseError as e:
            self.logger.error(f"[ChatHistoryService] clear_user_chat_history failed: {e}")
            raise

        return deleted_count
