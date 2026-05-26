"""Generic per-container Azure Cosmos DB repository.

Each ``CosmosRepository`` instance is bound to a single container and manages
its own ``CosmosClient`` lifecycle.  Create one instance per container via the
DI container or ``CosmosRepositoryFactory``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from azure.cosmos.aio import ContainerProxy, CosmosClient, DatabaseProxy
from azure.identity.aio import DefaultAzureCredential


class CosmosRepository:
    """Async CRUD repository for a single Azure Cosmos DB container.

    Args:
        endpoint: Cosmos DB account endpoint URL.  Mutually exclusive with
            ``connection_string``; ``endpoint`` is preferred for production
            (uses ``DefaultAzureCredential`` / managed identity).
        connection_string: Full connection string (dev/emulator only).
        database_name: Name of the Cosmos DB database.
        container_name: Name of the container this repository targets.

    Example — creating two repositories for different containers::

        workflows = CosmosRepository(
            endpoint="https://account.documents.azure.com:443/",
            database_name="app-db",
            container_name="workflows",
        )
        products = CosmosRepository(
            endpoint="https://account.documents.azure.com:443/",
            database_name="app-db",
            container_name="products",
        )
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        connection_string: str | None = None,
        database_name: str,
        container_name: str,
    ) -> None:
        if not endpoint and not connection_string:
            raise ValueError("CosmosRepository requires either 'endpoint' or 'connection_string'.")
        self._endpoint = endpoint
        self._connection_string = connection_string
        self._database_name = database_name
        self._container_name = container_name
        self._credential: DefaultAzureCredential | None = None
        self._client: CosmosClient | None = None
        self._container: ContainerProxy | None = None

    # ------------------------------------------------------------------
    # Internal initialisation
    # ------------------------------------------------------------------

    def _ensure_cosmos_client(self) -> CosmosClient:
        """Lazily create the ``CosmosClient``."""
        if self._client is None:
            if self._connection_string:
                self._client = CosmosClient.from_connection_string(self._connection_string)
            else:
                self._credential = DefaultAzureCredential()
                assert self._endpoint is not None
                self._client = CosmosClient(self._endpoint, credential=self._credential)
        return self._client

    def _ensure_container(self) -> ContainerProxy:
        """Lazily resolve the ``ContainerProxy``."""
        if self._container is None:
            client = self._ensure_cosmos_client()
            db: DatabaseProxy = client.get_database_client(self._database_name)
            self._container = db.get_container_client(self._container_name)
        return self._container

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def cosmos_client(self) -> CosmosClient:
        """Underlying ``CosmosClient`` — exposed for provisioning operations."""
        return self._ensure_cosmos_client()

    @property
    def database_name(self) -> str:
        return self._database_name

    @property
    def container_name(self) -> str:
        return self._container_name

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying ``CosmosClient`` and credential."""
        if self._client:
            await self._client.close()
        if self._credential:
            await self._credential.close()

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    async def create_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Create a new item; raises ``CosmosHttpResponseError`` if it exists."""
        return await self._ensure_container().create_item(body=item)

    async def upsert_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Create or replace an item (``id`` + partition key required)."""
        return await self._ensure_container().upsert_item(body=item)

    async def read_item(self, *, item_id: str, partition_key: list[str] | str) -> dict[str, Any]:
        """Point-read an item by id and partition key.

        Args:
            item_id: Document ``id``.
            partition_key: HPK list ``[user_id, serial_number]`` or single-value string.
        """
        return await self._ensure_container().read_item(item=item_id, partition_key=partition_key)

    async def replace_item(self, *, item_id: str, item: dict[str, Any]) -> dict[str, Any]:
        """Fully replace an existing item."""
        return await self._ensure_container().replace_item(item=item_id, body=item)

    async def patch_item(
        self,
        *,
        item_id: str,
        partition_key: list[str] | str,
        patch_operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Partially update an item using JSON Patch operations.

        Example::

            await repo.patch_item(
                item_id="abc",
                partition_key=["user1", "SN-001"],
                patch_operations=[{"op": "replace", "path": "/status", "value": "done"}],
            )
        """
        return await self._ensure_container().patch_item(
            item=item_id,
            partition_key=partition_key,
            patch_operations=patch_operations,
        )

    async def delete_item(self, *, item_id: str, partition_key: list[str] | str) -> None:
        """Delete an item by id and partition key."""
        await self._ensure_container().delete_item(item=item_id, partition_key=partition_key)

    def query_items(
        self,
        query: str,
        parameters: list[dict[str, Any]] | None = None,
        *,
        partition_key: list[str] | str | None = None,
        max_item_count: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute a SQL query and return an async iterator over matching documents.

        Pass ``partition_key`` to scope the query to a single logical partition
        and avoid cross-partition fan-out.

        Example::

            async for item in repo.query_items(
                "SELECT TOP 1 * FROM c WHERE c.serial_number = @sn ORDER BY c.created_at DESC",
                parameters=[{"name": "@sn", "value": "ABC123"}],
                partition_key=["system", "ABC123"],
            ):
                print(item)
        """
        container = self._ensure_container()
        kwargs: dict[str, Any] = {"query": query, "parameters": parameters}
        if partition_key is not None:
            kwargs["partition_key"] = partition_key
        if max_item_count is not None:
            kwargs["max_item_count"] = max_item_count
        return container.query_items(**kwargs)
