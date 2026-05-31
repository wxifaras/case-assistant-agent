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

from azure.cosmos.aio import CosmosClient
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient, SearchIndexerClient
from dependency_injector import containers, providers

from app.core.container_group_chat_workflow import build_chat_workflow_providers
from app.core.container_group_repositories import build_repository_providers
from app.core.container_group_search_ingestion import build_search_ingestion_providers
from app.core.container_group_sharepoint import build_sharepoint_providers
from app.core.container_helpers import (
    create_cosmos_client,
    create_search_client,
    create_search_index_client,
    create_search_indexer_client,
)
from app.core.logger import Logger, create_logger
from app.core.settings import Settings
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
from app.services.blob_storage_service import BlobStorageService


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
        create_search_index_client,
        options=search_service_options,
    )

    search_indexer_client: providers.Singleton[SearchIndexerClient] = providers.Singleton(
        create_search_indexer_client,
        options=search_service_options,
    )

    search_client: providers.Singleton[SearchClient] = providers.Singleton(
        create_search_client,
        options=search_service_options,
    )

    # CosmosClient is registered as Factory rather than Singleton because
    # the async client carries its own connection lifecycle (aenter/aexit).
    # A fresh client per injection avoids sharing async context state across
    # requests; the SDK handles its own internal connection pooling.
    cosmos_client: providers.Factory[CosmosClient] = providers.Factory(
        create_cosmos_client,
        options=cosmos_db_options,
    )

    (
        data_source_service,
        search_index_service,
        skillset_service,
        indexer_service,
        search_pipeline_orchestrator,
        search_service,
    ) = build_search_ingestion_providers(
        logger=logger,
        search_service_options=search_service_options,
        blob_storage_options=blob_storage_options,
        azure_openai_options=azure_openai_options,
        ai_services_options=ai_services_options,
        search_index_client=search_index_client,
        search_indexer_client=search_indexer_client,
        search_client=search_client,
    )

    cosmos_repository, sites_cosmos_repository = build_repository_providers(
        cosmos_db_options=cosmos_db_options,
    )

    (
        citation_tracker,
        query_rewriter,
        reflection_agent,
        answer_generator,
        pii_detection_service,
        foundry_service,
        agentic_rag_workflow,
        chat_history_service,
        chat_service,
    ) = build_chat_workflow_providers(
        config=config,
        logger=logger,
        workflow_options=workflow_options,
        azure_credential=azure_credential,
        search_service=search_service,
        foundry_agent_options=foundry_agent_options,
        pii_detection_options=pii_detection_options,
        cosmos_repository=cosmos_repository,
    )

    blob_storage_service: providers.Singleton[BlobStorageService] = providers.Singleton(
        BlobStorageService,
        settings=config,
    )

    (
        sharepoint_membership_service,
        sharepoint_graph_client,
        sharepoint_site_discovery_service,
        sharepoint_sync_state_store,
        sharepoint_file_sync_runner,
        sharepoint_transfer_service,
        sharepoint_sync_service,
        sharepoint_sync_queue_worker,
    ) = build_sharepoint_providers(
        config=config,
        logger=logger,
        blob_storage_service=blob_storage_service,
        sites_cosmos_repository=sites_cosmos_repository,
        search_pipeline_orchestrator=search_pipeline_orchestrator,
    )
