import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.chat import AgenticRAGState, Citation
from app.models.config_options import WorkflowOptions
from app.services.chat_service import ChatService


def _make_chat_service(chat_history_service=None) -> ChatService:
    workflow_builder = Mock()
    workflow_builder.build_workflow.return_value = Mock()
    return ChatService(
        logger=Mock(),
        workflow_options=WorkflowOptions(),
        chat_history_service=chat_history_service,
        workflow=workflow_builder,
    )


@pytest.mark.unit
def test_build_response_maps_final_state_fields() -> None:
    service = _make_chat_service()
    citation = Citation(document_id="doc-1", content_id="chunk-1")
    final_state = AgenticRAGState(
        query="How do I reset my password?",
        current_attempt=2,
        vetted_results=[],
        citations=[citation],
        answer="Use self-service password reset.",
        thought_process=[{"step": "response", "details": {"final_answer": "Use self-service password reset."}}],
        search_history=[{"query": "password reset", "results_count": 2, "attempt": 1}],
        decisions=["finalize"],
    )

    response = service._build_response(str(uuid.uuid4()), final_state)

    assert response.answer == "Use self-service password reset."
    assert response.citations == [citation]
    assert response.attempts == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consume_stream_returns_final_output() -> None:
    service = _make_chat_service()
    final_state = AgenticRAGState(query="Reset password")

    async def stream():
        yield SimpleNamespace(type="output", data=final_state, executor_id="answer")

    output = await service._consume_stream(stream())

    assert output is final_state


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_async_rejects_invalid_session_id() -> None:
    service = _make_chat_service()

    with pytest.raises(ValueError, match="Invalid session_id format"):
        await service.query_async(query="Reset password", session_id="not-a-uuid")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_user_chat_history_delegates_to_history_service() -> None:
    history_service = Mock()
    history_service.clear_user_chat_history = AsyncMock(return_value=3)
    service = _make_chat_service(chat_history_service=history_service)

    deleted = await service.clear_user_chat_history("user-1")

    assert deleted == 3
    history_service.clear_user_chat_history.assert_awaited_once_with("user-1")
