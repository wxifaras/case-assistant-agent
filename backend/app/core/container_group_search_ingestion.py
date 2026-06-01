"""Search and ingestion provider group builder for the DI container."""

from __future__ import annotations

from dependency_injector import providers

from app.ingestion.search.data_source_service import DataSourceService, IDataSourceService
from app.ingestion.search.indexer_service import IIndexerService, IndexerService
from app.ingestion.search.search_index_service import ISearchIndexService, SearchIndexService
from app.ingestion.search.search_pipeline_orchestrator import ISearchPipelineOrchestrator, SearchPipelineOrchestrator
from app.ingestion.search.skillset_service import ISkillsetService, SkillsetService
from app.services.search_service import ISearchService, SearchService


def build_search_ingestion_providers(
    *,
    logger,
    search_service_options,
    blob_storage_options,
    azure_openai_options,
    ai_services_options,
    search_index_client,
    search_indexer_client,
    search_client,
) -> tuple[
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
]:
    """Create provider objects for search, indexing, and ingestion workflows."""
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

    return (
        data_source_service,
        search_index_service,
        skillset_service,
        indexer_service,
        search_pipeline_orchestrator,
        search_service,
    )
