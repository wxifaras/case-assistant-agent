"""Search ingestion module.

Provides namespaced access to Azure AI Search ingestion services under
``app.ingestion.search`` while preserving compatibility with existing modules.
"""

from .data_source_service import DataSourceService, IDataSourceService
from .indexer_service import IIndexerService, IndexerService
from .search_index_service import ISearchIndexService, SearchIndexService
from .search_pipeline_orchestrator import ISearchPipelineOrchestrator, SearchPipelineOrchestrator
from .skillset_service import ISkillsetService, SkillsetService

__all__ = [
    "IDataSourceService",
    "DataSourceService",
    "ISearchIndexService",
    "SearchIndexService",
    "ISkillsetService",
    "SkillsetService",
    "IIndexerService",
    "IndexerService",
    "ISearchPipelineOrchestrator",
    "SearchPipelineOrchestrator",
]
