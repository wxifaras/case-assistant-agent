"""Unit tests for KnowledgeBaseService (REST against AI Search preview API)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.search.knowledge_base_service import (
    KnowledgeBaseService,
    _DEFAULT_API_VERSION,
)
from app.models.config_options import KnowledgeBaseOptions, KnowledgeSourceOptions


pytestmark = pytest.mark.unit


def _make_response(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    resp.json.return_value = json_body or {}
    return resp


def _make_credential() -> MagicMock:
    cred = MagicMock()
    cred.get_token = AsyncMock(return_value=SimpleNamespace(token="fake-token"))
    cred.close = AsyncMock()
    return cred


def _make_http_client() -> MagicMock:
    http = MagicMock()
    http.put = AsyncMock(return_value=_make_response(201))
    http.delete = AsyncMock(return_value=_make_response(204))
    http.get = AsyncMock(return_value=_make_response(200, {"name": "kb"}))
    http.aclose = AsyncMock()
    return http


def _make_options() -> KnowledgeBaseOptions:
    return KnowledgeBaseOptions(
        name="case-assistant-kb",
        description="Test KB",
        knowledge_sources=[
            KnowledgeSourceOptions(
                name="case-assistant-ks",
                index_name="case-assistant-content-index",
                description="ks desc",
            )
        ],
        aoai_endpoint="https://aifwxdev001.openai.azure.com",
        aoai_deployment_name="gpt-4.1",
        output_modality="extractive",
        default_reranker_threshold=2.0,
        max_output_size=5000,
        attempt_fast_path=True,
    )


@pytest.mark.asyncio
async def test_create_or_update_knowledge_source_puts_expected_payload():
    http = _make_http_client()
    svc = KnowledgeBaseService(
        search_endpoint="https://srchwxdev001.search.windows.net",
        credential=_make_credential(),
        http_client=http,
    )

    source = KnowledgeSourceOptions(
        name="ks1",
        index_name="idx1",
        description="hello",
        source_data_select=["field_a", "field_b"],
    )
    await svc.create_or_update_knowledge_source_async(source)

    http.put.assert_awaited_once()
    args, kwargs = http.put.call_args
    url = args[0] if args else kwargs.get("url")
    assert "/knowledgeSources/ks1" in url
    assert f"api-version={_DEFAULT_API_VERSION}" in url
    headers = kwargs["headers"]
    assert headers["Authorization"] == "Bearer fake-token"
    assert headers["Content-Type"] == "application/json"
    body = kwargs["json"]
    assert body == {
        "name": "ks1",
        "kind": "searchIndex",
        "searchIndexParameters": {
            "searchIndexName": "idx1",
            "sourceDataSelect": "field_a,field_b",
        },
        "description": "hello",
    }


@pytest.mark.asyncio
async def test_create_or_update_knowledge_base_provisions_sources_then_agent():
    http = _make_http_client()
    svc = KnowledgeBaseService(
        search_endpoint="https://srchwxdev001.search.windows.net",
        credential=_make_credential(),
        http_client=http,
    )

    await svc.create_or_update_knowledge_base_async(_make_options())

    assert http.put.await_count == 2  # one source + one agent

    # First call is the knowledge source
    source_call = http.put.await_args_list[0]
    assert "/knowledgeSources/case-assistant-ks" in source_call.args[0]

    # Second call is the knowledge agent
    agent_call = http.put.await_args_list[1]
    agent_url = agent_call.args[0]
    assert "/agents/case-assistant-kb" in agent_url
    assert f"api-version={_DEFAULT_API_VERSION}" in agent_url

    body = agent_call.kwargs["json"]
    assert body["name"] == "case-assistant-kb"
    assert body["outputConfiguration"]["modality"] == "extractive"
    assert body["outputConfiguration"]["attemptFastPath"] is True
    assert body["requestLimits"]["maxOutputSize"] == 5000
    assert body["models"][0]["azureOpenAIParameters"]["deploymentId"] == "gpt-4.1"
    assert (
        body["models"][0]["azureOpenAIParameters"]["resourceUri"]
        == "https://aifwxdev001.openai.azure.com"
    )
    assert body["knowledgeSources"][0]["name"] == "case-assistant-ks"
    assert body["knowledgeSources"][0]["rerankerThreshold"] == 2.0


@pytest.mark.asyncio
async def test_create_or_update_knowledge_base_rejects_missing_aoai():
    svc = KnowledgeBaseService(
        search_endpoint="https://srchwxdev001.search.windows.net",
        credential=_make_credential(),
        http_client=_make_http_client(),
    )
    opts = _make_options()
    opts.aoai_endpoint = None
    with pytest.raises(ValueError):
        await svc.create_or_update_knowledge_base_async(opts)


@pytest.mark.asyncio
async def test_delete_endpoints_treat_404_as_success():
    http = _make_http_client()
    http.delete = AsyncMock(return_value=_make_response(404))
    svc = KnowledgeBaseService(
        search_endpoint="https://srchwxdev001.search.windows.net",
        credential=_make_credential(),
        http_client=http,
    )
    await svc.delete_knowledge_base_async("kb-x")
    await svc.delete_knowledge_source_async("ks-x")
    assert http.delete.await_count == 2
    assert "/agents/kb-x" in http.delete.await_args_list[0].args[0]
    assert "/knowledgeSources/ks-x" in http.delete.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_get_knowledge_base_returns_none_on_404():
    http = _make_http_client()
    http.get = AsyncMock(return_value=_make_response(404))
    svc = KnowledgeBaseService(
        search_endpoint="https://srchwxdev001.search.windows.net",
        credential=_make_credential(),
        http_client=http,
    )
    result = await svc.get_knowledge_base_async("kb-x")
    assert result is None


@pytest.mark.asyncio
async def test_failed_put_raises_runtime_error():
    http = _make_http_client()
    http.put = AsyncMock(return_value=_make_response(500))
    svc = KnowledgeBaseService(
        search_endpoint="https://srchwxdev001.search.windows.net",
        credential=_make_credential(),
        http_client=http,
    )
    with pytest.raises(RuntimeError):
        await svc.create_or_update_knowledge_source_async(
            KnowledgeSourceOptions(name="ks1", index_name="idx1")
        )
