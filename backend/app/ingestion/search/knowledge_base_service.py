"""Azure AI Search Knowledge Source + Knowledge Agent ("Knowledge Base") provisioning.

Uses the Azure AI Search REST preview API (``2025-08-01-preview``) because the
typed Python SDK on PyPI does not yet expose knowledge-source / knowledge-agent
models. Authentication uses ``DefaultAzureCredential`` against the
``https://search.azure.com/.default`` scope.

RBAC requirements on the Search service for the caller running provisioning:
    * Search Service Contributor  (PUT/DELETE knowledgeSources, agents)
RBAC requirements on the AOAI/Foundry account for the Search service identity
(so the knowledge agent can call the model with AAD):
    * Cognitive Services OpenAI User
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential

from app.models.config_options import KnowledgeBaseOptions, KnowledgeSourceOptions

_SEARCH_SCOPE = "https://search.azure.com/.default"
_DEFAULT_API_VERSION = "2025-08-01-preview"

logger = logging.getLogger(__name__)


class IKnowledgeBaseService(ABC):
    """Interface for provisioning Search knowledge sources and knowledge agents."""

    @abstractmethod
    async def create_or_update_knowledge_source_async(self, source: KnowledgeSourceOptions) -> None:
        """Create or update a single knowledge source."""

    @abstractmethod
    async def create_or_update_knowledge_base_async(self, kb: KnowledgeBaseOptions) -> None:
        """Create or update the knowledge agent and provision its knowledge sources."""

    @abstractmethod
    async def delete_knowledge_base_async(self, name: str) -> None:
        """Delete a knowledge agent. Ignores 404."""

    @abstractmethod
    async def delete_knowledge_source_async(self, name: str) -> None:
        """Delete a knowledge source. Ignores 404."""

    @abstractmethod
    async def get_knowledge_base_async(self, name: str) -> dict[str, Any] | None:
        """Return the knowledge agent definition, or None if it does not exist."""


class KnowledgeBaseService(IKnowledgeBaseService):
    """REST-based implementation against the AI Search preview API."""

    def __init__(
        self,
        search_endpoint: str,
        credential: AsyncTokenCredential | None = None,
        api_version: str = _DEFAULT_API_VERSION,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not search_endpoint:
            raise ValueError("search_endpoint is required")
        self._endpoint = search_endpoint.rstrip("/")
        self._credential = credential or DefaultAzureCredential()
        self._owns_credential = credential is None
        self._api_version = api_version
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._owns_http = http_client is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
        if self._owns_credential:
            await self._credential.close()  # type: ignore[union-attr]

    async def _headers(self) -> dict[str, str]:
        token = await self._credential.get_token(_SEARCH_SCOPE)
        return {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self._endpoint}/{path}?api-version={self._api_version}"

    async def create_or_update_knowledge_source_async(self, source: KnowledgeSourceOptions) -> None:
        body = self._build_source_body(source)
        url = self._url(f"knowledgeSources/{source.name}")
        headers = await self._headers()
        resp = await self._http.put(url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Failed to PUT knowledge source '{source.name}': {resp.status_code} {resp.text}"
            )
        logger.info("Knowledge source '%s' upserted (HTTP %s)", source.name, resp.status_code)

    async def create_or_update_knowledge_base_async(self, kb: KnowledgeBaseOptions) -> None:
        if not kb.knowledge_sources:
            raise ValueError(f"Knowledge base '{kb.name}' has no knowledge_sources")
        if not kb.aoai_endpoint or not kb.aoai_deployment_name:
            raise ValueError(
                f"Knowledge base '{kb.name}' requires aoai_endpoint and aoai_deployment_name"
            )

        # Upsert each knowledge source first so the KB binding is valid.
        for source in kb.knowledge_sources:
            await self.create_or_update_knowledge_source_async(source)

        body = self._build_kb_body(kb)
        url = self._url(f"agents/{kb.name}")
        headers = await self._headers()
        resp = await self._http.put(url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Failed to PUT knowledge agent '{kb.name}': {resp.status_code} {resp.text}"
            )
        logger.info("Knowledge base '%s' upserted (HTTP %s)", kb.name, resp.status_code)

    async def delete_knowledge_base_async(self, name: str) -> None:
        url = self._url(f"agents/{name}")
        headers = await self._headers()
        resp = await self._http.delete(url, headers=headers)
        if resp.status_code in (200, 204, 404):
            logger.info("Knowledge base '%s' delete -> HTTP %s", name, resp.status_code)
            return
        raise RuntimeError(f"Failed to DELETE knowledge agent '{name}': {resp.status_code} {resp.text}")

    async def delete_knowledge_source_async(self, name: str) -> None:
        url = self._url(f"knowledgeSources/{name}")
        headers = await self._headers()
        resp = await self._http.delete(url, headers=headers)
        if resp.status_code in (200, 204, 404):
            logger.info("Knowledge source '%s' delete -> HTTP %s", name, resp.status_code)
            return
        raise RuntimeError(f"Failed to DELETE knowledge source '{name}': {resp.status_code} {resp.text}")

    async def get_knowledge_base_async(self, name: str) -> dict[str, Any] | None:
        url = self._url(f"agents/{name}")
        headers = await self._headers()
        resp = await self._http.get(url, headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Failed to GET knowledge agent '{name}': {resp.status_code} {resp.text}")
        return resp.json()

    # ------------------------------------------------------------------
    # Body builders
    # ------------------------------------------------------------------

    def _build_source_body(self, source: KnowledgeSourceOptions) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": source.name,
            "kind": source.kind,
            "searchIndexParameters": {
                "searchIndexName": source.index_name,
            },
        }
        if source.description:
            body["description"] = source.description
        if source.source_data_select:
            body["searchIndexParameters"]["sourceDataSelect"] = ",".join(source.source_data_select)
        return body

    def _build_kb_body(self, kb: KnowledgeBaseOptions) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": kb.name,
            "models": [
                {
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": kb.aoai_endpoint,
                        "deploymentId": kb.aoai_deployment_name,
                        "modelName": kb.aoai_deployment_name,
                        # authIdentity omitted -> Search service system-assigned identity
                    },
                }
            ],
            "knowledgeSources": [
                {
                    "name": source.name,
                    "includeReferences": True,
                    "includeReferenceSourceData": True,
                    "rerankerThreshold": kb.default_reranker_threshold,
                }
                for source in kb.knowledge_sources
            ],
            "outputConfiguration": {
                "modality": kb.output_modality,
                "attemptFastPath": kb.attempt_fast_path,
                "includeActivity": True,
            },
            "requestLimits": {
                "maxOutputSize": kb.max_output_size,
            },
        }
        if kb.description:
            body["description"] = kb.description
        if kb.retrieval_instructions:
            body["retrievalInstructions"] = kb.retrieval_instructions
        return body
