from unittest.mock import AsyncMock, Mock

import pytest

from app.models import AzureOpenAIOptions, RetrievedDocument
from app.services.search_service import SearchService


def _make_openai_options(model: str = "text-embedding-3-large") -> AzureOpenAIOptions:
    return AzureOpenAIOptions(
        resource_uri="https://openai.example.azure.com",
        text_embedding_model=model,
        chat_completion_model="gpt-4o",
    )


def _make_service(model: str = "text-embedding-3-large") -> SearchService:
    search_client = Mock()
    search_client.close = AsyncMock()
    return SearchService(
        search_client=search_client,
        openai_options=_make_openai_options(model),
        logger=Mock(),
    )


def _make_document(content_id: str, content: str, reranker_score: float | None = 3.0) -> RetrievedDocument:
    return RetrievedDocument(
        document_id=f"doc-{content_id}",
        content_id=content_id,
        title=f"Title {content_id}",
        content=content,
        source=f"kb/{content_id}.md",
        score=0.9,
        reranker_score=reranker_score,
    )


@pytest.mark.unit
def test_build_filter_expression_combines_custom_filter_and_exclusions() -> None:
    service = _make_service()

    filter_expr = service._build_filter_expression(
        {"custom": "category eq 'identity'", "document_type": "pdf"},
        exclude_ids=["chunk-1", "o'hara"],
    )

    assert filter_expr == "category eq 'identity' and not search.in(content_id, 'chunk-1,o''hara', ',')"


@pytest.mark.unit
def test_parse_results_prefers_text_document_id_and_image_fallback() -> None:
    service = _make_service()

    documents = service._parse_results(
        [
            {
                "content_id": "chunk-1",
                "text_document_id": "doc-text",
                "document_title": "Password Reset",
                "content_text": "Reset via SSPR",
                "content_path": "kb/reset.md",
                "location_metadata": {"pageNumber": 2},
                "@search.score": 1.2,
                "@search.reranker_score": 3.1,
            },
            {
                "content_id": "chunk-2",
                "image_document_id": "doc-image",
                "document_title": "Diagram",
                "content_text": "Image explanation",
                "content_path": "kb/diagram.png",
                "location_metadata": {},
                "@search.score": 0.8,
            },
        ]
    )

    assert [doc.document_id for doc in documents] == ["doc-text", "doc-image"]
    assert documents[0].page_number == 2


@pytest.mark.unit
def test_deduplicate_results_removes_exact_and_near_duplicates() -> None:
    service = _make_service()
    documents = [
        _make_document("a", "reset your password with self service"),
        _make_document("a", "reset your password with self service"),
        _make_document("b", "reset your password with self service"),
        _make_document("c", "open a helpdesk ticket for support"),
    ]

    deduplicated = service._deduplicate_results(documents, similarity_threshold=0.9)

    assert [doc.content_id for doc in deduplicated] == ["a", "c"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_async_filters_low_reranker_scores_and_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service()
    monkeypatch.setattr(service, "generate_embedding_async", AsyncMock(return_value=[0.1, 0.2]))
    monkeypatch.setattr(
        service,
        "_search_with_retry",
        AsyncMock(
            return_value=[
                {
                    "content_id": "chunk-1",
                    "text_document_id": "doc-1",
                    "document_title": "Password Reset",
                    "content_text": "reset your password",
                    "content_path": "kb/reset.md",
                    "location_metadata": {},
                    "@search.score": 1.0,
                    "@search.reranker_score": 3.2,
                },
                {
                    "content_id": "chunk-2",
                    "text_document_id": "doc-2",
                    "document_title": "Password Reset Copy",
                    "content_text": "reset your password",
                    "content_path": "kb/reset-copy.md",
                    "location_metadata": {},
                    "@search.score": 0.9,
                    "@search.reranker_score": 3.0,
                },
                {
                    "content_id": "chunk-3",
                    "text_document_id": "doc-3",
                    "document_title": "Irrelevant",
                    "content_text": "some other text",
                    "content_path": "kb/other.md",
                    "location_metadata": {},
                    "@search.score": 0.4,
                    "@search.reranker_score": 1.0,
                },
            ]
        ),
    )

    documents = await service.search_async("reset password", search_mode="hybrid")

    assert [doc.content_id for doc in documents] == ["chunk-1"]


@pytest.mark.unit
def test_get_embedding_dimensions_uses_model_lookup_with_fallback() -> None:
    assert _make_service("text-embedding-3-large").get_embedding_dimensions() == 3072
    assert _make_service("unknown-model").get_embedding_dimensions() == 1536
