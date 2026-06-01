"""Shared FastAPI dependencies for dependency injection.

Provides reusable dependency functions that can be injected into route
handlers via ``fastapi.Depends()``.  All service/settings dependencies
resolve through the ``Container`` singleton from ``app.core.container``.

Note:
    ``get_container`` and ``get_logger`` use deferred imports to avoid a
    circular import: ``app.api.main`` imports this module, so importing
    ``container`` and ``logger`` at module level would create a cycle.
    The deferred pattern resolves the values at call time instead.
"""

from fastapi import Depends

from app.core.container import Container
from app.core.logger import Logger
from app.core.settings import Settings
from app.ingestion.search.indexer_service import IIndexerService
from app.ingestion.search.search_pipeline_orchestrator import ISearchPipelineOrchestrator
from app.ingestion.sharepoint.sharepoint_sync_service import ISharePointSyncService
from app.services.chat_history_service import IChatHistoryService
from app.services.chat_service import IChatService
from app.services.pii_detection_service import IPIIDetectionService


def get_container() -> Container:
    """Return the application DI container.

    The import is deferred to break the circular dependency with
    ``app.api.main``, which itself imports this module.

    Returns:
        The ``Container`` singleton wiring all services and clients.
    """
    from app.api.main import container

    return container


def get_logger() -> Logger:
    """Return the application-wide logger.

    The import is deferred to break the circular dependency with
    ``app.api.main``, which itself imports this module.

    Returns:
        The ``Logger`` instance configured with Application Insights.
    """
    from app.api.main import logger

    return logger


def get_settings(container: Container = Depends(get_container)) -> Settings:
    """Return application settings from the DI container.

    Returns:
        The ``Settings`` singleton populated from env-vars / ``.env``.
    """
    return container.config()


def get_indexer_service(container: Container = Depends(get_container)) -> IIndexerService:
    """Return the indexer service from the DI container.

    Returns:
        ``IIndexerService`` implementation wired by the container.
    """
    return container.indexer_service()


def get_search_pipeline_orchestrator(container: Container = Depends(get_container)) -> ISearchPipelineOrchestrator:
    """Return the search pipeline orchestrator from the DI container.

    Returns:
        ``ISearchPipelineOrchestrator`` implementation wired by the container.
    """
    return container.search_pipeline_orchestrator()


def get_chat_service(container: Container = Depends(get_container)) -> IChatService:
    """Return the chat service from the DI container.

    Returns:
        ``IChatService`` implementation wired by the container.
    """
    return container.chat_service()


def get_chat_history_service(container: Container = Depends(get_container)) -> IChatHistoryService:
    """Return the chat history service from the DI container.

    Returns:
        ``IChatHistoryService`` implementation wired by the container.
    """
    return container.chat_history_service()


def get_pii_detection_service(container: Container = Depends(get_container)) -> IPIIDetectionService:
    """Return the PII detection service from the DI container.

    Used to scan user prompts (and optionally LLM responses) for personally
    identifiable information via Azure AI Language before they reach the
    RAG workflow. See ``app.services.pii_detection_service`` for the full
    list of supported entity categories.

    Returns:
        ``IPIIDetectionService`` implementation wired by the container.
    """
    return container.pii_detection_service()


def get_sharepoint_sync_service(container: Container = Depends(get_container)) -> ISharePointSyncService:
    """Return the SharePoint -> Blob sync service from the DI container.

    Returns:
        ``ISharePointSyncService`` implementation wired by the container.
    """
    return container.sharepoint_sync_service()
