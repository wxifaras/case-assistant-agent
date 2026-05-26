"""Agentic RAG chat service.

This module provides the agentic RAG workflow orchestration via the
Microsoft Agent Framework (MAF). All search, reflection, and answer
generation steps are delegated to the configured workflow.

Pre-flight PII guard
--------------------
When a ``PIIDetectionService`` and ``PIIDetectionOptions`` are injected and
``options.enabled`` is True, every incoming user prompt is scanned with
Azure AI Language before the workflow runs. If the scanner flags any
entity above the configured confidence threshold and ``block_on_detection``
is True, ``query_async`` short-circuits and returns a canned refusal
response instead of invoking retrieval / LLM calls. This keeps sensitive
user input out of search-index queries, the OpenAI completion request,
and the Cosmos DB conversation log.

When ``options.redact_responses`` is also True, the final assistant
answer is re-scanned post-generation and any detected PII is replaced
with ``*`` characters before being returned and persisted — a belt-and-
braces defense against the model echoing sensitive content back.
"""

import uuid
from abc import ABC, abstractmethod
from typing import Any

from agent_framework import Message, WorkflowEvent

from app.api.schemas.chat import ChatHistoryMessage, QueryResponse
from app.core.logger import Logger
from app.models.chat import AgenticRAGState
from app.models.config_options import PIIDetectionOptions, WorkflowOptions
from app.services.pii_detection_service import IPIIDetectionService
from app.utils.citation_tracker import CitationTracker
from app.workflows.core import AgenticRAGWorkflow


class IChatService(ABC):
    """Interface for agentic RAG chat service operations."""

    @abstractmethod
    async def query_async(
        self,
        query: str,
        session_id: str,
        user_id: str | None = None,
        chat_history: list[ChatHistoryMessage] | None = None,
        filters: Any | None = None,
    ) -> QueryResponse:
        """Execute agentic RAG query using workflow orchestration."""
        pass

    @abstractmethod
    async def get_user_chat_history(
        self,
        session_id: str,
        user_id: str,
        max_messages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve chat history for a session."""
        pass

    @abstractmethod
    async def list_user_chat_sessions(
        self,
        user_id: str,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """List all chat sessions for a user."""
        pass

    @abstractmethod
    async def delete_user_chat_session(
        self,
        session_id: str,
        user_id: str,
    ) -> None:
        """Delete a chat session."""
        pass

    @abstractmethod
    async def clear_user_chat_history(
        self,
        user_id: str,
    ) -> int:
        """Clear all chat history for a user."""
        pass


class ChatService(IChatService):
    """Agentic RAG chat service backed by a MAF workflow.

    Delegates all retrieval, reflection, and answer generation to the
    configured AgenticRAGWorkflow. Optionally persists conversation
    history to Cosmos DB via ``chat_history_service`` and optionally
    screens incoming prompts for PII via ``pii_detection_service``.
    """

    # Canned refusal returned when PII is detected and block_on_detection=True.
    # Format-ready: the set of detected categories is interpolated at call time.
    _PII_REFUSAL_TEMPLATE: str = (
        "I noticed your message appears to contain personal or sensitive "
        "information ({categories}). To keep your data safe, I'm not going "
        "to process this request. Please rephrase your question without "
        "sharing personal details (like names, phone numbers, emails, "
        "account numbers, or IDs) and try again."
    )

    def __init__(
        self,
        logger: Logger,
        workflow_options: WorkflowOptions,
        chat_history_service=None,
        workflow: AgenticRAGWorkflow | None = None,
        citation_tracker: CitationTracker | None = None,
        pii_detection_service: IPIIDetectionService | None = None,
        pii_detection_options: PIIDetectionOptions | None = None,
    ) -> None:
        """Initialize the chat service.

        Args:
            logger: Application logger.
            workflow_options: Workflow execution configuration.
            chat_history_service: Optional Cosmos-backed chat history persistence.
            workflow: Builder used to construct a fresh :class:`Workflow` per request.
        """
        self.logger: Logger = logger
        self._workflow_options: WorkflowOptions = workflow_options
        self._chat_history_service = chat_history_service
        self._workflow_builder = workflow

        if workflow is None:
            raise ValueError("ChatService requires an AgenticRAGWorkflow builder.")
        if not hasattr(workflow, "build_workflow"):
            raise ValueError("ChatService.workflow must expose build_workflow(name=...)")

        # PII guard dependencies — both optional so the service still runs
        # when the Language endpoint is not configured.
        self._pii_service: IPIIDetectionService | None = pii_detection_service
        self._pii_options: PIIDetectionOptions | None = pii_detection_options

        if self._pii_service and self._pii_options and self._pii_options.enabled:
            self.logger.info(
                f"ChatService PII guard enabled "
                f"(block_on_detection={self._pii_options.block_on_detection}, "
                f"min_confidence={self._pii_options.min_confidence}, "
                f"redact_responses={self._pii_options.redact_responses})"
            )
        else:
            self.logger.info("ChatService PII guard disabled (service or options not provided / disabled)")

    # ------------------------------------------------------------------
    # Internal: PII guard
    # ------------------------------------------------------------------

    async def _check_prompt_for_pii(
        self,
        query: str,
        session_id: str,
        user_id: str | None,
    ) -> QueryResponse | None:
        """Scan the user's prompt for PII and build a refusal response if found.

        Returns ``None`` when the caller should proceed with the normal
        RAG workflow — either because the guard is disabled, no PII was
        detected, ``block_on_detection`` is False, or the scanner failed.

        Returns a populated ``QueryResponse`` when the caller must return
        it directly without invoking the workflow.
        """
        if not (self._pii_service and self._pii_options and self._pii_options.enabled):
            return None

        try:
            result = await self._pii_service.detect_pii_async(
                query,
                language=self._pii_options.language,
                min_confidence=self._pii_options.min_confidence,
                categories_filter=self._pii_options.categories_filter,
            )
        except Exception as exc:
            # Fail open: log and proceed. The RAG workflow still runs so a
            # misconfigured Language endpoint doesn't take chat down with it.
            self.logger.error(f"PII scan failed, proceeding without guard: {exc}")
            return None

        if not result.contains_pii:
            return None

        categories = sorted({e.category for e in result.entities})
        self.logger.warning(
            f"[PII Guard] Detected {len(result.entities)} entities in prompt "
            f"(session={session_id}, user={user_id}): {categories}"
        )

        if not self._pii_options.block_on_detection:
            # Annotate-only mode — log and continue with the workflow.
            return None

        refusal_text = self._PII_REFUSAL_TEMPLATE.format(categories=", ".join(categories))

        return QueryResponse(
            answer=refusal_text,
            citations=[],
            document_count=0,
            session_id=session_id,
            thought_process=[
                {
                    "step": "pii_guard",
                    "details": {
                        "blocked": True,
                        "entity_count": len(result.entities),
                        "categories": categories,
                    },
                    "attempt": 0,
                }
            ],
            search_history=[],
            decisions=["pii_blocked"],
            attempts=0,
        )

    async def _maybe_redact_answer(self, answer: str) -> str:
        """Redact PII from a generated answer when ``redact_responses`` is enabled.

        Returns the original answer unchanged when redaction is disabled or
        the scanner fails.
        """
        if not (
            self._pii_service
            and self._pii_options
            and self._pii_options.enabled
            and self._pii_options.redact_responses
            and answer
        ):
            return answer

        try:
            return await self._pii_service.redact_pii_async(
                answer,
                language=self._pii_options.language,
                min_confidence=self._pii_options.min_confidence,
                categories_filter=self._pii_options.categories_filter,
            )
        except Exception as exc:
            self.logger.error(f"Answer-side PII redaction failed, returning original: {exc}")
            return answer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query_async(
        self,
        query: str,
        session_id: str,
        user_id: str | None = None,
        chat_history: list[ChatHistoryMessage] | None = None,
        filters: Any | None = None,
    ) -> QueryResponse:
        """Execute agentic RAG query using MAF workflow orchestration."""
        try:
            uuid.UUID(session_id)
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid session_id format. Must be a valid UUID. Error: {e}") from e

        workflow_name = f"agentic-rag::{user_id or 'anonymous'}::{session_id}"

        # ------------------------------------------------------------------
        # Fresh run
        # ------------------------------------------------------------------
        self.logger.info(f"Executing agentic RAG query: {query[:100]}...")

        # ------------------------------------------------------------------
        # PII guard — short-circuits before any retrieval / LLM call if the
        # prompt contains sensitive information.
        # ------------------------------------------------------------------
        pii_response = await self._check_prompt_for_pii(query, session_id, user_id)
        if pii_response is not None:
            # Persist the blocked turn so the UI can display it and so
            # downstream auditing tools can see what happened. We store
            # the refusal, NOT the original sensitive prompt.
            if self._chat_history_service and user_id:
                try:
                    await self._chat_history_service.add_user_chat_message(
                        session_id=session_id,
                        user_id=user_id,
                        role="user",
                        content="[redacted: user prompt blocked by PII guard]",
                    )
                    await self._chat_history_service.add_user_chat_message(
                        session_id=session_id,
                        user_id=user_id,
                        role="assistant",
                        content=pii_response.answer,
                        metadata={"pii_blocked": True},
                    )
                except Exception as e:
                    self.logger.error(f"Failed to persist PII-blocked turn: {e}")
            return pii_response

        conv_history = None
        if self._chat_history_service and user_id:
            try:
                conv_history = await self._chat_history_service.get_user_chat_history(
                    session_id=session_id,
                    user_id=user_id,
                    max_messages=self._workflow_options.chat_history_window,
                )
                self.logger.info(f"Loaded {len(conv_history)} messages from chat history")
            except Exception as e:
                self.logger.warning(f"Failed to load chat history: {e}")

        if not conv_history and chat_history:
            conv_history = [Message(role=msg.role.value, contents=[msg.content]) for msg in chat_history]

        chat_history_dict: list[dict[str, str]] = []
        if conv_history:
            for msg in conv_history:
                chat_history_dict.append({"role": "user" if msg.role == "user" else "assistant", "content": msg.text})

        filters_dict = None
        if filters:
            filters_dict = filters.model_dump() if hasattr(filters, "model_dump") else filters

        initial_state = AgenticRAGState(
            query=query,
            user_id=user_id,
            session_id=session_id,
            chat_history=chat_history_dict,
            filters=filters_dict,
            max_attempts=self._workflow_options.max_retrieval_iterations,
        )

        self.logger.info(
            f"[WORKFLOW START] Executing MAF workflow for query: {query[:50]!r} (workflow={workflow_name})"
        )

        workflow = self._build_workflow(workflow_name)
        stream = workflow.run(initial_state, stream=True)
        final_state = await self._consume_stream(stream)

        if final_state is None:
            raise Exception("Workflow did not produce output")

        # Optional: post-generation redaction of any PII the model may have
        # produced (e.g. echoed from retrieved context).
        answer_text = final_state.answer or "Unable to generate answer"
        answer_text = await self._maybe_redact_answer(answer_text)

        if self._chat_history_service and user_id:
            try:
                await self._chat_history_service.add_user_chat_message(
                    session_id=session_id, user_id=user_id, role="user", content=query
                )
                await self._chat_history_service.add_user_chat_message(
                    session_id=session_id,
                    user_id=user_id,
                    role="assistant",
                    content=answer_text,
                    metadata={
                        "citations": [c.model_dump() for c in (final_state.citations or [])],
                        "document_count": len(final_state.vetted_results or []),
                    },
                )
                self.logger.info(f"Saved conversation to Cosmos DB: {session_id}")
            except Exception as e:
                self.logger.error(f"Failed to save conversation: {e}")
        await self._maybe_persist_history(session_id, user_id, query, final_state)
        return self._build_response(session_id, final_state)

    # ------------------------------------------------------------------
    # Resume / build helpers
    # ------------------------------------------------------------------

    def _build_workflow(self, workflow_name: str):
        """Build a fresh workflow instance."""
        assert self._workflow_builder is not None  # narrowed by __init__
        return self._workflow_builder.build_workflow(name=workflow_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _consume_stream(self, stream) -> AgenticRAGState | None:
        """Drain a workflow event stream and return the final output state."""
        final_state: AgenticRAGState | None = None

        async for event in stream:
            event: WorkflowEvent  # type: ignore[no-redef]
            self.logger.debug(f"[WORKFLOW EVENT] type={event.type}, executor={getattr(event, 'executor_id', 'N/A')}")
            if event.type == "output":
                final_state = event.data
                self.logger.info(f"Workflow output from executor: {getattr(event, 'executor_id', 'unknown')}")

        return final_state

    def _build_response(self, session_id: str, final_state: AgenticRAGState) -> QueryResponse:
        answer_text = final_state.answer or ""
        response = QueryResponse(
            answer=answer_text,
            citations=final_state.citations or [],
            document_count=len(final_state.vetted_results or []),
            session_id=session_id,
            thought_process=final_state.thought_process or [],
            search_history=final_state.search_history or [],
            decisions=final_state.decisions or [],
            attempts=final_state.current_attempt,
        )
        self.logger.info(f"Query completed: {len(response.answer)} chars, {len(response.citations)} citations")
        return response

    async def _maybe_persist_history(
        self, session_id: str, user_id: str | None, query: str, final_state: AgenticRAGState
    ) -> None:
        if not (self._chat_history_service and user_id):
            return
        try:
            await self._chat_history_service.add_user_chat_message(
                session_id=session_id, user_id=user_id, role="user", content=query
            )
            await self._chat_history_service.add_user_chat_message(
                session_id=session_id,
                user_id=user_id,
                role="assistant",
                content=final_state.answer or "Unable to generate answer",
                metadata={
                    "citations": [c.model_dump() for c in (final_state.citations or [])],
                    "document_count": len(final_state.vetted_results or []),
                },
            )
            self.logger.info(f"Saved conversation to Cosmos DB: {session_id}")
        except Exception as e:
            self.logger.error(f"Failed to save conversation: {e}")

    async def get_user_chat_history(
        self, session_id: str, user_id: str, max_messages: int | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve chat history for a session."""
        if not self._chat_history_service:
            raise ValueError("Chat history storage not available. Configure Cosmos DB.")

        messages = await self._chat_history_service.get_user_chat_history(
            session_id=session_id, user_id=user_id, max_messages=max_messages
        )
        return [
            {
                "role": msg.role.value if hasattr(msg.role, "value") else str(msg.role),
                "text": msg.text,
                "id": msg.message_id,
            }
            for msg in messages
        ]

    async def list_user_chat_sessions(self, user_id: str, max_results: int | None = None) -> list[dict[str, Any]]:
        """List all chat sessions for a user."""
        if not self._chat_history_service:
            raise ValueError("Chat history storage not available. Configure Cosmos DB.")

        sessions = await self._chat_history_service.list_user_chat_sessions(user_id, max_results)
        return sessions or []

    async def delete_user_chat_session(self, session_id: str, user_id: str) -> None:
        """Delete a chat session."""
        if not self._chat_history_service:
            raise ValueError("Chat history storage not available. Configure Cosmos DB.")

        await self._chat_history_service.delete_user_chat_session(session_id, user_id)

    async def clear_user_chat_history(self, user_id: str) -> int:
        """Clear all chat history for a user."""
        if not self._chat_history_service:
            raise ValueError("Chat history storage not available. Configure Cosmos DB.")

        return await self._chat_history_service.clear_user_chat_history(user_id)
