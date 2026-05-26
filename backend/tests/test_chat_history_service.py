import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.chat_history_service import ChatHistoryService


def _make_service() -> tuple[ChatHistoryService, Mock, Mock]:
    repo = Mock()
    logger = Mock()
    return ChatHistoryService(repo=repo, logger=logger), repo, logger


@pytest.mark.unit
def test_sanitize_message_text_removes_citations_and_extra_spacing() -> None:
    text = "Reset your password [1] using SSPR {chunk-123}.  Follow the steps .\n\n\nThanks"

    sanitized = ChatHistoryService.sanitize_message_text(text)

    assert sanitized == "Reset your password using SSPR. Follow the steps.\n\nThanks"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_chat_history_prefers_sanitized_message_text(
    make_async_iterable: Callable[[list[dict[str, Any]]], AsyncIterator[dict[str, Any]]],
) -> None:
    service, repo, _ = _make_service()
    repo.query_items.return_value = make_async_iterable(
        [
            {
                "serialized_message": json.dumps({"id": "m1", "role": "assistant", "text": "raw [1] text"}),
                "message_text": "clean text",
                "message_id": "m1",
                "role": "assistant",
            }
        ]
    )

    messages = await service.get_user_chat_history("session-1", "user-1")

    assert len(messages) == 1
    assert messages[0].text == "clean text"
    assert messages[0].message_id == "m1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_add_user_chat_message_sanitizes_before_upsert() -> None:
    service, repo, _ = _make_service()
    repo.upsert_item = AsyncMock()

    await service.add_user_chat_message(
        session_id="session-1",
        user_id="user-1",
        role="assistant",
        content="Use SSPR [1] {chunk-9}",
        metadata={"source": "kb"},
    )

    repo.upsert_item.assert_awaited_once()
    stored = repo.upsert_item.await_args.args[0]
    payload = json.loads(stored["serialized_message"])
    assert stored["message_text"] == "Use SSPR"
    assert payload["text"] == "Use SSPR"
    assert payload["source"] == "kb"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_user_chat_history_deletes_all_items_and_returns_count(
    make_async_iterable: Callable[[list[dict[str, str]]], AsyncIterator[dict[str, str]]],
) -> None:
    service, repo, logger = _make_service()
    repo.query_items.return_value = make_async_iterable(
        [
            {"id": "one", "user_id": "user-1", "session_id": "session-a"},
            {"id": "two", "user_id": "user-1", "session_id": "session-b"},
        ]
    )
    repo.delete_item = AsyncMock()

    deleted_count = await service.clear_user_chat_history("user-1")

    assert deleted_count == 2
    assert repo.delete_item.await_count == 2
    repo.delete_item.assert_any_await(item_id="one", partition_key=["user-1", "session-a"])
    repo.delete_item.assert_any_await(item_id="two", partition_key=["user-1", "session-b"])
    logger.info.assert_called_once()
