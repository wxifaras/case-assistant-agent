"""Indexer service for Azure AI Search."""

from abc import ABC, abstractmethod

from azure.search.documents.indexes.aio import SearchIndexerClient
from azure.search.documents.indexes.models import (
    FieldMapping,
    IndexingParameters,
    IndexingParametersConfiguration,
    SearchIndexer,
    SearchIndexerStatus,
)


class IIndexerService(ABC):
    """Interface for indexer service operations."""

    @abstractmethod
    async def create_indexer_async(
        self,
        indexer_name: str,
        data_source_name: str,
        target_index_name: str,
        skillset_name: str,
    ) -> None:
        """
        Create or update a search indexer.

        Args:
            indexer_name: The name of the indexer to create.
            data_source_name: The name of the data source to index from.
            target_index_name: The name of the target search index.
            skillset_name: The name of the skillset to apply.
        """
        pass

    @abstractmethod
    async def run_indexer_async(self, indexer_name: str) -> None:
        """
        Run a search indexer to process documents.

        Args:
            indexer_name: The name of the indexer to run.
        """
        pass

    @abstractmethod
    async def get_indexer_status_async(self, indexer_name: str) -> SearchIndexerStatus:
        """
        Get the status of a search indexer.

        Args:
            indexer_name: The name of the indexer to check.

        Returns:
            The status of the indexer.
        """
        pass

    @abstractmethod
    async def reset_indexer_async(self, indexer_name: str) -> None:
        """
        Reset a search indexer to reprocess all documents.

        Args:
            indexer_name: The name of the indexer to reset.
        """
        pass

    @abstractmethod
    async def create_markdown_indexer_async(
        self,
        indexer_name: str,
        data_source_name: str,
        target_index_name: str,
        skillset_name: str,
    ) -> None:
        """
        Create or update a markdown parsing indexer (oneToMany mode).

        Args:
            indexer_name: The name of the indexer to create.
            data_source_name: The name of the data source to index from.
            target_index_name: The name of the target search index.
            skillset_name: The name of the skillset to apply.
        """
        pass

    @abstractmethod
    async def create_json_indexer_async(
        self,
        indexer_name: str,
        data_source_name: str,
        target_index_name: str,
        skillset_name: str,
    ) -> None:
        """
        Create or update a JSON parsing indexer.

        Args:
            indexer_name: The name of the indexer to create.
            data_source_name: The name of the data source to index from.
            target_index_name: The name of the target search index.
            skillset_name: The name of the skillset to apply.
        """
        pass


class IndexerService(IIndexerService):
    """
    Service for managing Azure AI Search indexers.

    Handles creation, execution, and monitoring of search indexers
    that process documents through skillsets and populate indexes.
    """

    def __init__(self, indexer_client: SearchIndexerClient, logger) -> None:
        """
        Initialize the IndexerService.

        Args:
            indexer_client: Azure Search indexer client for managing indexers.
        """
        self._indexer_client: SearchIndexerClient = indexer_client
        self.logger = logger

    async def create_indexer_async(
        self,
        indexer_name: str,
        data_source_name: str,
        target_index_name: str,
        skillset_name: str,
    ) -> None:
        """
        Create or update a search indexer with skillset integration.

        The indexer is configured with:
        - Skillset for multimodal enrichment
        - Field mappings for metadata
        - Indexing parameters for batch processing
        - File data extraction settings

        Args:
            indexer_name: The name of the indexer to create.
            data_source_name: The name of the data source to index from.
            target_index_name: The name of the target search index.
            skillset_name: The name of the skillset to apply.

        Raises:
            Exception: If indexer creation fails.
        """
        field_mappings = [
            FieldMapping(
                source_field_name="metadata_storage_name",
                target_field_name="document_title",
            )
        ]

        indexer = SearchIndexer(
            name=indexer_name,
            data_source_name=data_source_name,
            target_index_name=target_index_name,
            skillset_name=skillset_name,
            field_mappings=field_mappings,
            parameters=IndexingParameters(
                max_failed_items=-1,
                max_failed_items_per_batch=-1,
                batch_size=1,
                configuration=IndexingParametersConfiguration(
                    allow_skillset_to_read_file_data=True,
                    data_to_extract="contentAndMetadata",
                    parsing_mode="default",
                    excluded_file_name_extensions=".md,.json,.txt",
                    query_timeout=None,  # type: ignore[arg-type]  # queryTimeout is not supported for azureblob data sources
                ),
            ),
        )

        await self._indexer_client.create_or_update_indexer(indexer)
        self.logger.info(f"Indexer '{indexer_name}' created or updated successfully.")

    async def run_indexer_async(self, indexer_name: str) -> None:
        """
        Run a search indexer to process documents.

        Initiates indexer execution asynchronously. The indexer will:
        - Pull data from the configured data source
        - Apply skillset enrichments
        - Populate the target search index

        Args:
            indexer_name: The name of the indexer to run.

        Raises:
            Exception: If indexer execution fails.
        """
        self.logger.info(f"Running indexer '{indexer_name}'...")
        await self._indexer_client.run_indexer(indexer_name)
        self.logger.info(f"Indexer '{indexer_name}' started successfully.")

    async def get_indexer_status_async(self, indexer_name: str) -> SearchIndexerStatus:
        """
        Get the status of a search indexer.

        Returns detailed execution history, error information, and
        processing statistics for the indexer.

        Args:
            indexer_name: The name of the indexer to check.

        Returns:
            The status of the indexer including execution history.

        Raises:
            ResourceNotFoundError: If the indexer does not exist.
            Exception: If status retrieval fails.
        """
        status = await self._indexer_client.get_indexer_status(indexer_name)
        return status

    async def reset_indexer_async(self, indexer_name: str) -> None:
        """
        Reset a search indexer to reprocess all documents.

        Clears indexer state and change tracking, causing all documents
        to be reprocessed on the next run.

        Args:
            indexer_name: The name of the indexer to reset.

        Raises:
            Exception: If indexer reset fails.
        """
        self.logger.info(f"Resetting indexer '{indexer_name}'...")
        await self._indexer_client.reset_indexer(indexer_name)
        self.logger.info(f"Indexer '{indexer_name}' reset successfully.")

    async def create_markdown_indexer_async(
        self,
        indexer_name: str,
        data_source_name: str,
        target_index_name: str,
        skillset_name: str,
    ) -> None:
        """
        Create or update a markdown parsing indexer using oneToMany mode.

        The indexer splits each markdown file into multiple search documents
        based on heading structure.  An AzureOpenAIEmbeddingSkill in the
        associated skillset generates the ``content_embedding`` vector.

        Field mappings:
        - ``/content`` → ``content_text`` (section body text)
        - ``/sections/h1`` → ``document_title`` (top-level heading context)

        Output field mappings:
        - ``/document/content_embedding`` → ``content_embedding``

        Args:
            indexer_name: The name of the indexer to create.
            data_source_name: The name of the data source to index from.
            target_index_name: The name of the target search index.
            skillset_name: The name of the skillset to apply.
        """
        field_mappings = [
            FieldMapping(
                source_field_name="metadata_storage_name",
                target_field_name="document_title",
            ),
        ]

        indexer = SearchIndexer(
            name=indexer_name,
            data_source_name=data_source_name,
            target_index_name=target_index_name,
            skillset_name=skillset_name,
            field_mappings=field_mappings,
            parameters=IndexingParameters(
                max_failed_items=-1,
                max_failed_items_per_batch=-1,
                configuration=IndexingParametersConfiguration(
                    parsing_mode="markdown",
                    markdown_parsing_submode="oneToMany",
                    data_to_extract="contentAndMetadata",
                    indexed_file_name_extensions=".md",
                    query_timeout=None,  # type: ignore[arg-type]  # queryTimeout is not supported for azureblob data sources
                ),
            ),
        )

        await self._indexer_client.create_or_update_indexer(indexer)
        self.logger.info(f"Markdown indexer '{indexer_name}' created or updated successfully.")

    async def create_json_indexer_async(
        self,
        indexer_name: str,
        data_source_name: str,
        target_index_name: str,
        skillset_name: str,
    ) -> None:
        """
        Create or update a JSON parsing indexer.

        Each JSON blob is indexed as a single search document.  The full text
        content of the blob is embedded via the associated skillset.

        Field mappings:
        - ``metadata_storage_name`` → ``document_title``

        Output field mappings:
        - ``/document/content_embedding`` → ``content_embedding``
        - ``/document/content`` → ``content_text``

        Args:
            indexer_name: The name of the indexer to create.
            data_source_name: The name of the data source to index from.
            target_index_name: The name of the target search index.
            skillset_name: The name of the skillset to apply.
        """
        field_mappings = [
            FieldMapping(
                source_field_name="metadata_storage_name",
                target_field_name="document_title",
            ),
        ]

        indexer = SearchIndexer(
            name=indexer_name,
            data_source_name=data_source_name,
            target_index_name=target_index_name,
            skillset_name=skillset_name,
            field_mappings=field_mappings,
            parameters=IndexingParameters(
                max_failed_items=-1,
                max_failed_items_per_batch=-1,
                configuration=IndexingParametersConfiguration(
                    parsing_mode="default",
                    data_to_extract="contentAndMetadata",
                    indexed_file_name_extensions=".json",
                    query_timeout=None,  # type: ignore[arg-type]  # queryTimeout is not supported for azureblob data sources
                ),
            ),
        )

        await self._indexer_client.create_or_update_indexer(indexer)
        self.logger.info(f"JSON indexer '{indexer_name}' created or updated successfully.")
