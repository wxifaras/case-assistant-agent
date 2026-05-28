"""Data source service for Azure AI Search.

Manages the creation and configuration of blob storage data source
connections used by the AI Search indexer, including change-detection
and soft-delete tracking policies.
"""

from abc import ABC, abstractmethod

from azure.search.documents.indexes.aio import SearchIndexerClient
from azure.search.documents.indexes.models import (
    HighWaterMarkChangeDetectionPolicy,
    SearchIndexerDataContainer,
    SearchIndexerDataSourceConnection,
    SearchIndexerDataSourceType,
    SoftDeleteColumnDeletionDetectionPolicy,
)

from app.models.config_options import BlobStorageOptions


class IDataSourceService(ABC):
    """Abstract interface for Azure AI Search data source service operations."""

    @abstractmethod
    async def create_blob_data_source_async(self, data_source_name: str) -> None:
        """Create or update a blob data source connection for Azure AI Search.

        Args:
            data_source_name: The name of the data source to create.
        """
        pass


class DataSourceService(IDataSourceService):
    """Service for managing Azure AI Search data source connections.

    Handles creation and configuration of blob storage data sources with
    change detection and deletion tracking policies.  Supports both managed
    identity (recommended for production) and connection string authentication.
    """

    def __init__(
        self,
        indexer_client: SearchIndexerClient,
        blob_options: BlobStorageOptions,
        logger,
    ) -> None:
        """Initialize the DataSourceService.

        Args:
            indexer_client: Azure Search indexer client for managing data sources.
            blob_options: Configuration options for blob storage.
            logger: Injected logging service.
        """
        self._indexer_client: SearchIndexerClient = indexer_client
        self._blob_options: BlobStorageOptions = blob_options
        self.logger = logger

    async def create_blob_data_source_async(self, data_source_name: str) -> None:
        """
        Create or update a blob data source connection for Azure AI Search.

        Configures the data source with:
        - High water mark change detection (based on last modified timestamp)
        - Soft delete detection policy (using metadata flag)

        Supports two authentication methods:
        - Managed identity: Uses resource_id (recommended for production)
        - Connection string: Uses connection_string (for local development/Azurite)

        Args:
            data_source_name: The name of the data source to create.

        Raises:
            Exception: If data source creation fails.
            ValueError: If neither resource_id nor connection_string is configured.
        """
        container: SearchIndexerDataContainer = SearchIndexerDataContainer(name=self._blob_options.container_name)

        # Determine which authentication method to use
        if self._blob_options.resource_id:
            # Managed identity authentication
            connection_string = f"ResourceId={self._blob_options.resource_id};"
            self.logger.info("Using managed identity authentication for blob data source")
        elif self._blob_options.connection_string:
            # Connection string authentication
            connection_string = self._blob_options.connection_string
            self.logger.info("Using connection string authentication for blob data source")
        else:
            raise ValueError("Either BlobStorage__ResourceId or BlobStorageConnection must be configured")

        data_source: SearchIndexerDataSourceConnection = SearchIndexerDataSourceConnection(
            name=data_source_name,
            type=SearchIndexerDataSourceType.AZURE_BLOB,
            connection_string=connection_string,
            container=container,
            description="A data source to store multi-modality documents",
            data_change_detection_policy=HighWaterMarkChangeDetectionPolicy(
                high_water_mark_column_name="metadata_storage_last_modified"
            ),
            data_deletion_detection_policy=SoftDeleteColumnDeletionDetectionPolicy(
                soft_delete_column_name="metadata_storage_file_deleted",
                soft_delete_marker_value="true",
            ),
        )

        await self._indexer_client.create_or_update_data_source_connection(data_source)
        self.logger.info(f"Data source '{data_source_name}' created or updated.")
