"""Dependency injection container for Case Assistant services.

Manages service lifecycle and dependency wiring using ``dependency-injector``.

Purpose:
    - Wire up service dependencies with automatic injection.
    - Provide application configuration to all components.
    - Enable testability through provider overrides in unit tests.
    - Ensure proper resource lifecycle management.

Module-level helpers:
    ``_make_search_credential(options)``
        Centralises Azure AI Search credential selection — returns an
        ``AzureKeyCredential`` when an API key is present, otherwise a
        ``DefaultAzureCredential`` (managed identity / CLI login).  Used
        by all three ``_create_search_*_client`` factories so auth logic
        lives in exactly one place.

    ``_create_cosmos_client(options)``
        Builds an async ``CosmosClient`` from a connection string or endpoint.

    ``_create_search_index_client(options)``
    ``_create_search_indexer_client(options)``
    ``_create_search_client(options)``
        Thin factories for the three Azure AI Search async clients used in
        the ingestion pipeline and query path.

Usage in FastAPI (main.py):
    ```python
    from app.core.container import Container

    container = Container()
    container.wire(modules=[
        "app.api.routes.chat",
        "app.api.routes.health",
        "app.api.routes.pipeline",
    ])

    app = FastAPI()
    app.container = container
    ```

Testing with provider overrides:
    ```python
    from dependency_injector import providers

    container = Container()
    container.cosmos_client.override(providers.Factory(MockCosmosClient))
    container.search_service.override(providers.Factory(MockSearchService))
    ```

Provider types and when to use them:

    Singleton — one instance shared across all requests.
        Use for:
        ✓ Azure Search SDK clients (SearchClient, SearchIndexClient, SearchIndexerClient)
        ✓ Configuration (Settings) and Logger
        ✓ Stateless ingestion services (DataSourceService, IndexerService, …)
        ✓ Services with expensive initialisation (connection pooling)
        Benefit: reuse connection pools, minimize startup overhead.

    Factory — a fresh instance per injection.
        Use for:
        ✓ CosmosClient — carries async connection context; injected fresh per request.
        ✓ Agents and workflow (QueryRewriter, ReflectionAgent, AnswerGenerator,
          AgenticRAGWorkflow) — stateful; isolated per request for thread safety.
        ✓ ChatService and ConversationService — own per-request conversation state.
        Benefit: isolation, no shared mutable state, safe for async concurrency.
"""

from azure.core.credentials import AzureKeyCredential
from azure.cosmos.aio import CosmosClient
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient, SearchIndexerClient
from dependency_injector import containers, providers

from app.agents.answer_generator import AnswerGenerator
from app.agents.query_rewriter import QueryRewriter
from app.agents.reflection_agent import ReflectionAgent
from app.core.logger import Logger, create_logger
from app.core.settings import Settings
from app.ingestion.data_source_service import DataSourceService, IDataSourceService
from app.ingestion.indexer_service import IIndexerService, IndexerService
from app.ingestion.search_index_service import ISearchIndexService, SearchIndexService
from app.ingestion.search_pipeline_orchestrator import ISearchPipelineOrchestrator, SearchPipelineOrchestrator
from app.ingestion.skillset_service import ISkillsetService, SkillsetService
from app.models import (
    AIServicesOptions,
    APIOptions,
    ApplicationInsightsOptions,
    AzureAIFoundryOptions,
    AzureOpenAIOptions,
    BlobStorageOptions,
    CosmosDBOptions,
    FoundryAgentOptions,
    KeyVaultOptions,
    PIIDetectionOptions,
    SearchServiceOptions,
    WorkflowOptions,
)
from app.repositories.cosmos_repository import CosmosRepository
from app.services.chat_history_service import ChatHistoryService, IChatHistoryService
from app.services.chat_service import ChatService, IChatService
from app.services.foundry_service import FoundryService, IFoundryService
from app.services.pii_detection_service import IPIIDetectionService, PIIDetectionService
from app.services.search_service import ISearchService, SearchService
from app.utils.citation_tracker import CitationTracker
from app.workflows.core import AgenticRAGWorkflow


def _make_search_credential(
    options: SearchServiceOptions,
) -> AzureKeyCredential | DefaultAzureCredential:
    """Return the appropriate credential for Azure AI Search.

    Uses ``AzureKeyCredential`` when an API key is configured, otherwise
    falls back to ``DefaultAzureCredential`` (managed identity / CLI login).

    Args:
        options: Search service configuration.

    Returns:
        An Azure credential suitable for passing to any Search*Client.
    """
    return AzureKeyCredential(options.api_key) if options.api_key else DefaultAzureCredential()


def _create_cosmos_client(options: CosmosDBOptions) -> CosmosClient:
    """Create a ``CosmosClient`` from a connection string or endpoint.

    Args:
        options: Cosmos DB configuration.

    Returns:
        An async ``CosmosClient``.

    Raises:
        ValueError: If neither ``connection_string`` nor ``endpoint`` is set.
    """
    if options.connection_string:
        return CosmosClient.from_connection_string(options.connection_string)
    elif options.endpoint:
        return CosmosClient(options.endpoint, credential=DefaultAzureCredential())
    else:
        raise ValueError("CosmosDBOptions must include either connection_string or endpoint.")


def _create_search_index_client(options: SearchServiceOptions) -> SearchIndexClient:
    """Create a ``SearchIndexClient`` using the appropriate credential.

    Args:
        options: Search service configuration.

    Returns:
        An async ``SearchIndexClient``.
    """
    return SearchIndexClient(endpoint=options.endpoint, credential=_make_search_credential(options))


def _create_search_indexer_client(options: SearchServiceOptions) -> SearchIndexerClient:
    """Create a ``SearchIndexerClient`` using the appropriate credential.

    Args:
        options: Search service configuration.

    Returns:
        An async ``SearchIndexerClient``.
    """
    return SearchIndexerClient(endpoint=options.endpoint, credential=_make_search_credential(options))


def _create_search_client(options: SearchServiceOptions) -> SearchClient:
    """Create a ``SearchClient`` for querying the configured index.

    Args:
        options: Search service configuration.

    Returns:
        An async ``SearchClient``.
    """
    return SearchClient(
        endpoint=options.endpoint,
        index_name=options.index_name,
        credential=_make_search_credential(options),
    )


class Container(containers.DeclarativeContainer):
    """
    Dependency injection container for application services and clients.
    """

    config: providers.Singleton[Settings] = providers.Singleton(Settings)

    azure_credential: providers.Singleton[SyncDefaultAzureCredential | None] = providers.Singleton(
        lambda c: SyncDefaultAzureCredential() if c.use_managed_identity else None,
        c=config,
    )

    logger: providers.Singleton[Logger] = providers.Singleton(create_logger, name="case-assistant-agent")

    search_service_options: providers.Singleton[SearchServiceOptions] = providers.Singleton(
        lambda c: c.search_service,
        c=config,
    )

    blob_storage_options: providers.Singleton[BlobStorageOptions] = providers.Singleton(
        lambda c: c.blob_storage,
        c=config,
    )

    ai_services_options: providers.Singleton[AIServicesOptions] = providers.Singleton(
        lambda c: c.ai_services,
        c=config,
    )

    azure_openai_options: providers.Singleton[AzureOpenAIOptions] = providers.Singleton(
        lambda c: c.azure_openai_options,
        c=config,
    )

    azure_ai_foundry_options: providers.Singleton[AzureAIFoundryOptions] = providers.Singleton(
        lambda c: c.azure_ai_foundry_options,
        c=config,
    )

    cosmos_db_options: providers.Singleton[CosmosDBOptions] = providers.Singleton(
        lambda c: c.cosmos_db_options,
        c=config,
    )

    key_vault_options: providers.Singleton[KeyVaultOptions] = providers.Singleton(
        lambda c: c.key_vault_options,
        c=config,
    )

    app_insights_options: providers.Singleton[ApplicationInsightsOptions] = providers.Singleton(
        lambda c: c.app_insights_options,
        c=config,
    )

    workflow_options: providers.Singleton[WorkflowOptions] = providers.Singleton(
        lambda c: c.workflow_options,
        c=config,
    )

    pii_detection_options: providers.Singleton[PIIDetectionOptions] = providers.Singleton(
        lambda c: c.pii_detection_options,
        c=config,
    )

    api_options: providers.Singleton[APIOptions] = providers.Singleton(
        lambda c: c.api_options,
        c=config,
    )

    foundry_agent_options: providers.Singleton[FoundryAgentOptions] = providers.Singleton(
        lambda c: c.foundry_agent_options,
        c=config,
    )

    search_index_client: providers.Singleton[SearchIndexClient] = providers.Singleton(
        _create_search_index_client,
        options=search_service_options,
    )

    search_indexer_client: providers.Singleton[SearchIndexerClient] = providers.Singleton(
        _create_search_indexer_client,
        options=search_service_options,
    )

    search_client: providers.Singleton[SearchClient] = providers.Singleton(
        _create_search_client,
        options=search_service_options,
    )

    # CosmosClient is registered as Factory rather than Singleton because
    # the async client carries its own connection lifecycle (aenter/aexit).
    # A fresh client per injection avoids sharing async context state across
    # requests; the SDK handles its own internal connection pooling.
    cosmos_client: providers.Factory[CosmosClient] = providers.Factory(
        _create_cosmos_client,
        options=cosmos_db_options,
    )

    data_source_service: providers.Singleton[IDataSourceService] = providers.Singleton(
        DataSourceService,
        indexer_client=search_indexer_client,
        blob_options=blob_storage_options,
        logger=logger,
    )

    search_index_service: providers.Singleton[ISearchIndexService] = providers.Singleton(
        SearchIndexService,
        index_client=search_index_client,
        openai_options=azure_openai_options,
        logger=logger,
    )

    skillset_service: providers.Singleton[ISkillsetService] = providers.Singleton(
        SkillsetService,
        search_indexer_client=search_indexer_client,
        search_options=search_service_options,
        openai_options=azure_openai_options,
        ai_services_options=ai_services_options,
        blob_options=blob_storage_options,
        logger=logger,
    )

    indexer_service: providers.Singleton[IIndexerService] = providers.Singleton(
        IndexerService,
        indexer_client=search_indexer_client,
        logger=logger,
    )

    search_pipeline_orchestrator: providers.Singleton[ISearchPipelineOrchestrator] = providers.Singleton(
        SearchPipelineOrchestrator,
        data_source_service=data_source_service,
        search_index_service=search_index_service,
        skillset_service=skillset_service,
        indexer_service=indexer_service,
        search_options=search_service_options,
        logger=logger,
    )

    search_service: providers.Factory[ISearchService] = providers.Factory(
        SearchService,
        search_client=search_client,
        openai_options=azure_openai_options,
        logger=logger,
        min_reranker_score=search_service_options.provided.min_reranker_score,
    )

    citation_tracker: providers.Factory[CitationTracker] = providers.Factory(
        CitationTracker,
        logger=logger,
    )

    query_rewriter: providers.Factory[QueryRewriter] = providers.Factory(
        QueryRewriter,
        settings=config,
        logger=logger,
        credential=azure_credential,
    )

    reflection_agent: providers.Factory[ReflectionAgent] = providers.Factory(
        ReflectionAgent,
        settings=config,
        logger=logger,
        workflow_options=workflow_options,
        credential=azure_credential,
    )

    answer_generator: providers.Factory[AnswerGenerator] = providers.Factory(
        AnswerGenerator,
        settings=config,
        logger=logger,
        citation_tracker=citation_tracker,
        credential=azure_credential,
    )
    pii_detection_service: providers.Singleton[IPIIDetectionService] = providers.Singleton(
        PIIDetectionService,
        settings=config,
        logger=logger,
    )

    foundry_service: providers.Factory[IFoundryService] = providers.Factory(
        FoundryService,
        options=foundry_agent_options,
        logger=logger,
    )

    cosmos_repository: providers.Singleton[CosmosRepository] = providers.Singleton(
        lambda opts: CosmosRepository(
            endpoint=opts.endpoint or None,
            connection_string=opts.connection_string or None,
            database_name=opts.database_name,
            container_name=opts.container_name,
        ),
        opts=cosmos_db_options,
    )

    agentic_rag_workflow: providers.Factory[AgenticRAGWorkflow] = providers.Factory(
        AgenticRAGWorkflow,
        settings=config,
        logger=logger,
        workflow_options=workflow_options,
        search_service=search_service,
        citation_tracker=citation_tracker,
        query_rewriter=query_rewriter,
        answer_generator=answer_generator,
        reflection_agent=reflection_agent,
        pii_detection_service=pii_detection_service,
        pii_detection_options=pii_detection_options,
    )

    chat_history_service: providers.Factory[IChatHistoryService] = providers.Factory(
        ChatHistoryService,
        repo=cosmos_repository,
        logger=logger,
    )

    chat_service: providers.Factory[IChatService] = providers.Factory(
        ChatService,
        logger=logger,
        workflow_options=workflow_options,
        chat_history_service=chat_history_service,
        workflow=agentic_rag_workflow,
        foundry_service=foundry_service,
        foundry_agent_options=foundry_agent_options,
        pii_detection_service=pii_detection_service,
        pii_detection_options=pii_detection_options,
    )
