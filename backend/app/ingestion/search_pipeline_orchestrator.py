"""Search pipeline orchestrator for coordinating multimodal indexing.

Coordinates the creation and execution of all Azure AI Search pipeline
components (data source, index, skillset, indexer) in the correct order.
"""

from abc import ABC, abstractmethod

from azure.core.exceptions import ResourceNotFoundError

from app.ingestion.data_source_service import IDataSourceService
from app.ingestion.indexer_service import IIndexerService
from app.ingestion.search_index_service import ISearchIndexService
from app.ingestion.skillset_service import ISkillsetService
from app.models.config_options import SearchServiceOptions


class ISearchPipelineOrchestrator(ABC):
    """Abstract interface for search pipeline orchestration operations."""

    @abstractmethod
    async def setup_pipeline_async(self) -> None:
        """Set up the complete search pipeline infrastructure.

        Creates all required components in order:
        1. Data source connection
        2. Search index
        3. Skillset
        4. Indexer
        """
        pass

    @abstractmethod
    async def run_indexer_async(self) -> None:
        """Run the indexer to process documents.

        Initiates document processing through the configured pipeline.
        """
        pass

    @abstractmethod
    async def is_first_run_async(self) -> bool:
        """Check if this is the first run of the pipeline.

        Returns:
            ``True`` if the indexer doesn't exist yet, ``False`` otherwise.
        """
        pass


class SearchPipelineOrchestrator(ISearchPipelineOrchestrator):
    """Orchestrates the Azure AI Search multimodal pipeline.

    Coordinates the setup and execution of:
    - Data source connections
    - Search indexes with vector capabilities
    - Skillsets for document enrichment
    - Indexers for document processing

    Ensures all components are created in the correct order and manages
    the overall pipeline lifecycle.
    """

    def __init__(
        self,
        data_source_service: IDataSourceService,
        search_index_service: ISearchIndexService,
        skillset_service: ISkillsetService,
        indexer_service: IIndexerService,
        search_options: SearchServiceOptions,
        logger,
    ) -> None:
        """Initialize the SearchPipelineOrchestrator.

        Args:
            data_source_service: Service for managing data source connections.
            search_index_service: Service for managing search indexes.
            skillset_service: Service for managing skillsets.
            indexer_service: Service for managing indexers.
            search_options: Configuration options for the search service.
            logger: Injected logging service.
        """
        self._data_source_service: IDataSourceService = data_source_service
        self._search_index_service: ISearchIndexService = search_index_service
        self._skillset_service: ISkillsetService = skillset_service
        self._indexer_service: IIndexerService = indexer_service
        self._search_options: SearchServiceOptions = search_options
        self.logger = logger

    async def setup_pipeline_async(self) -> None:
        """Set up the complete search pipeline infrastructure.

        Creates all required components in the correct order:
        1. Blob data source connection (for document input)
        2. Search index (for storing enriched content)
        3. Skillset (for multimodal enrichment)
        4. Indexer (for coordinating the pipeline)

        Each step is logged for observability.

        Raises:
            Exception: If any pipeline component fails to create.
        """
        self.logger.info("Setting up search pipeline...")

        # Step 1: Create blob data source
        await self._data_source_service.create_blob_data_source_async(self._search_options.data_source_name)
        self.logger.info("Blob data source created.")

        # Step 2: Create search index
        await self._search_index_service.create_search_index_async(self._search_options.index_name)
        self.logger.info("Search index created.")

        # Step 3: Create skillset
        await self._skillset_service.create_skillset_using_sdk_async(
            self._search_options.skillset_name, self._search_options.index_name
        )
        self.logger.info("Multimodal skillset created.")

        # Step 4: Create multimodal indexer
        await self._indexer_service.create_indexer_async(
            self._search_options.indexer_name,
            self._search_options.data_source_name,
            self._search_options.index_name,
            self._search_options.skillset_name,
        )
        self.logger.info("Multimodal indexer created.")

        # Step 5: Create markdown skillset + indexer
        await self._skillset_service.create_markdown_skillset_async(
            self._search_options.markdown_skillset_name,
            self._search_options.index_name,
        )
        self.logger.info("Markdown skillset created.")

        await self._indexer_service.create_markdown_indexer_async(
            self._search_options.markdown_indexer_name,
            self._search_options.data_source_name,
            self._search_options.index_name,
            self._search_options.markdown_skillset_name,
        )
        self.logger.info("Markdown indexer created.")

        # Step 6: Create JSON skillset + indexer
        await self._skillset_service.create_json_skillset_async(
            self._search_options.json_skillset_name,
            self._search_options.index_name,
        )
        self.logger.info("JSON skillset created.")

        await self._indexer_service.create_json_indexer_async(
            self._search_options.json_indexer_name,
            self._search_options.data_source_name,
            self._search_options.index_name,
            self._search_options.json_skillset_name,
        )
        self.logger.info("JSON indexer created.")

        self.logger.info("Search pipeline setup complete.")

    async def run_indexer_async(self) -> None:
        """Run all indexers to process documents.

        Initiates execution of the multimodal, markdown, and JSON indexers to:
        - Pull documents from the shared data source
        - Apply skillset enrichments (text extraction, embeddings, etc.)
        - Populate the search index with enriched content

        Each indexer targets its respective file types via
        ``includedFileNameExtensions`` / ``excludedFileNameExtensions``.

        The indexers run asynchronously in Azure. Use the indexer service's
        status methods to monitor progress.

        Raises:
            Exception: If any indexer execution fails to start.
        """
        self.logger.info("Running all indexers...")
        await self._indexer_service.run_indexer_async(self._search_options.indexer_name)
        self.logger.info("Multimodal indexer run initiated.")

        await self._indexer_service.run_indexer_async(self._search_options.markdown_indexer_name)
        self.logger.info("Markdown indexer run initiated.")

        await self._indexer_service.run_indexer_async(self._search_options.json_indexer_name)
        self.logger.info("JSON indexer run initiated.")

    async def is_first_run_async(self) -> bool:
        """Check if this is the first run of the pipeline.

        Determines whether the indexer has been created before by
        attempting to retrieve its status. A 404 error indicates
        the indexer doesn't exist yet.

        Returns:
            ``True`` if the indexer doesn't exist (first run), ``False`` otherwise.

        Raises:
            Exception: If status check fails for reasons other than 404.
        """
        try:
            # Try to get indexer status - if it exists, this is not first run
            await self._indexer_service.get_indexer_status_async(self._search_options.indexer_name)
            self.logger.info("Indexer exists. Not first run.")
            return False
        except ResourceNotFoundError:
            self.logger.info("Indexer not found. This is the first run.")
            return True
