"""Repository provider group builder for the DI container."""

from __future__ import annotations

from dependency_injector import providers

from app.repositories.cosmos_repository import CosmosRepository


def build_repository_providers(*, cosmos_db_options) -> tuple[providers.Provider, providers.Provider]:
    """Create Cosmos repository providers used by chat and SharePoint flows."""
    cosmos_repository: providers.Singleton[CosmosRepository] = providers.Singleton(
        lambda opts: CosmosRepository(
            endpoint=opts.endpoint or None,
            connection_string=opts.connection_string or None,
            database_name=opts.database_name,
            container_name=opts.container_name,
        ),
        opts=cosmos_db_options,
    )

    sites_cosmos_repository: providers.Singleton[CosmosRepository] = providers.Singleton(
        lambda opts: CosmosRepository(
            endpoint=opts.endpoint or None,
            connection_string=opts.connection_string or None,
            database_name=opts.database_name,
            container_name=opts.sites_container_name,
        ),
        opts=cosmos_db_options,
    )

    return cosmos_repository, sites_cosmos_repository
