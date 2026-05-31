from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from app.core.settings import Settings


class BlobStorageService:
    """
    Async Azure Blob Storage operations.

    Provides upload, download, and delete operations for workflow artifacts.
    Uses DefaultAzureCredential for passwordless authentication.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialize Blob Storage service.

        Args:
            settings: Application settings containing blob_storage_account_url

        Note:
            Clients are created lazily on first use to allow API startup
            even when storage is not configured.
        """
        self._settings = settings
        self._credential: DefaultAzureCredential | None = None
        self._blob_service_client: BlobServiceClient | None = None

    def _ensure_client(self) -> BlobServiceClient:
        """Ensure blob service client is initialized."""
        if self._blob_service_client is None:
            if not self._settings.blob_storage.account_url:
                raise ValueError("Blob Storage account_url is not configured")
            self._credential = DefaultAzureCredential()
            self._blob_service_client = BlobServiceClient(
                account_url=self._settings.blob_storage.account_url, credential=self._credential
            )
        return self._blob_service_client

    async def close(self) -> None:
        """Close the underlying async BlobServiceClient and credential, releasing all connections."""
        if self._blob_service_client:
            await self._blob_service_client.close()
        if self._credential:
            await self._credential.close()

    async def upload_artifact(
        self,
        container: str,
        blob_name: str,
        data: bytes,
        *,
        overwrite: bool = True,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """
        Upload artifact data to blob storage.

        Args:
            container: Container name
            blob_name: Blob name (path within container)
            data: Binary data to upload
            overwrite: Whether to overwrite existing blob (default: True)
            metadata: Optional key-value metadata to attach to blob

        Returns:
            Blob URL

        Example:
            >>> url = await blob_service.upload_artifact(
            ...     container="artifacts",
            ...     blob_name="workflow-123/input.jpg",
            ...     data=image_bytes,
            ...     metadata={"source": "sharepoint", "item_id": "123"}
            ... )
        """
        client = self._ensure_client()
        container_client = client.get_container_client(container)

        # Ensure container exists
        try:
            await container_client.create_container()
        except Exception:
            # Container already exists, continue
            pass

        blob_client = container_client.get_blob_client(blob_name)
        if metadata is None:
            await blob_client.upload_blob(data, overwrite=overwrite)
        else:
            await blob_client.upload_blob(data, overwrite=overwrite, metadata=metadata)

        return blob_client.url

    async def upload_artifact_stream(
        self,
        container: str,
        blob_name: str,
        data: AsyncIterable[bytes],
        *,
        length: int | None = None,
        overwrite: bool = True,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload streamed artifact data to blob storage.

        Args:
            container: Container name.
            blob_name: Blob name (path within container).
            data: Async iterable yielding bytes chunks.
            length: Optional known total byte length.
            overwrite: Whether to overwrite existing blob (default: True).
            metadata: Optional key-value metadata to attach to blob.

        Returns:
            Blob URL.
        """
        client = self._ensure_client()
        container_client = client.get_container_client(container)

        try:
            await container_client.create_container()
        except Exception:
            pass

        blob_client = container_client.get_blob_client(blob_name)
        if metadata is None:
            await blob_client.upload_blob(data, length=length, overwrite=overwrite)
        else:
            await blob_client.upload_blob(data, length=length, overwrite=overwrite, metadata=metadata)

        return blob_client.url

    async def download_artifact(self, container: str, blob_name: str) -> bytes:
        """
        Download artifact data from blob storage.

        Args:
            container: Container name
            blob_name: Blob name (path within container)

        Returns:
            Binary blob data

        Raises:
            Exception: If blob not found or download fails

        Example:
            >>> data = await blob_service.download_artifact(
            ...     container="artifacts",
            ...     blob_name="workflow-123/input.jpg"
            ... )
        """
        client = self._ensure_client()
        container_client = client.get_container_client(container)
        blob_client = container_client.get_blob_client(blob_name)

        downloader = await blob_client.download_blob()
        return await downloader.readall()

    async def generate_sas_url(
        self,
        container: str,
        blob_name: str,
        *,
        ttl_minutes: int = 15,
    ) -> str:
        """Generate a short-lived read-only SAS URL for a blob.

        Uses a user delegation key (no storage account key required),
        compatible with DefaultAzureCredential / managed identity.

        Args:
            container: Container name.
            blob_name: Blob name (path within container).
            ttl_minutes: How long the SAS URL should remain valid (default: 15 min).

        Returns:
            Full SAS URL string.
        """
        client = self._ensure_client()
        now = datetime.now(UTC)
        expiry = now + timedelta(minutes=ttl_minutes)

        user_delegation_key = await client.get_user_delegation_key(
            key_start_time=now,
            key_expiry_time=expiry,
        )

        account_name = client.account_name
        if not account_name:
            raise ValueError("Could not determine storage account name from BlobServiceClient")

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container,
            blob_name=blob_name,
            user_delegation_key=user_delegation_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )

        return f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}?{sas_token}"

    async def delete_artifact(self, container: str, blob_name: str) -> None:
        """
        Delete artifact from blob storage.

        Args:
            container: Container name
            blob_name: Blob name (path within container)

        Raises:
            Exception: If blob not found or delete fails

        Example:
            >>> await blob_service.delete_artifact(
            ...     container="artifacts",
            ...     blob_name="workflow-123/input.jpg"
            ... )
        """
        client = self._ensure_client()
        container_client = client.get_container_client(container)
        blob_client = container_client.get_blob_client(blob_name)
        await blob_client.delete_blob()

    async def list_blobs(self, container: str, prefix: str | None = None) -> list[str]:
        """
        List blob names in a container with optional prefix filter.

        Args:
            container: Container name
            prefix: Optional prefix to filter blobs (e.g., "workflow-123/")

        Returns:
            List of blob names

        Example:
            >>> blobs = await blob_service.list_blobs(
            ...     container="artifacts",
            ...     prefix="workflow-123/"
            ... )
        """
        client = self._ensure_client()
        container_client = client.get_container_client(container)
        blob_names = []

        async for blob in container_client.list_blobs(name_starts_with=prefix):
            blob_names.append(blob.name)

        return blob_names

    async def list_blob_items_with_metadata(self, container: str, prefix: str | None = None) -> list[dict[str, Any]]:
        """List blobs with metadata for reconciliation operations."""
        client = self._ensure_client()
        container_client = client.get_container_client(container)
        items: list[dict[str, Any]] = []

        async for blob in container_client.list_blobs(name_starts_with=prefix, include=["metadata"]):
            items.append({"name": blob.name, "metadata": dict(blob.metadata or {})})

        return items

    async def get_blob_metadata(self, container: str, blob_name: str) -> dict[str, str] | None:
        """Fetch blob metadata, returning None when the blob does not exist."""
        client = self._ensure_client()
        container_client = client.get_container_client(container)
        blob_client = container_client.get_blob_client(blob_name)

        try:
            properties = await blob_client.get_blob_properties()
        except ResourceNotFoundError:
            return None

        return dict(properties.metadata or {})

    async def set_blob_metadata(self, container: str, blob_name: str, metadata: dict[str, str]) -> None:
        """Replace metadata for a blob if it exists."""
        client = self._ensure_client()
        container_client = client.get_container_client(container)
        blob_client = container_client.get_blob_client(blob_name)

        await blob_client.set_blob_metadata(metadata=metadata)
