"""SharePoint provider group builder for the DI container."""

from __future__ import annotations

from dependency_injector import providers

from app.ingestion.sharepoint import (
    SharePointFileSyncRunner,
    SharePointSyncStateStore,
    SharePointTransferService,
)
from app.ingestion.sharepoint.httpx_graph_adapter import HttpxGraphAdapterFactory
from app.ingestion.sharepoint.msgraph_adapter import MsgraphGraphAdapterFactory
from app.ingestion.sharepoint.sharepoint_sync_service import ISharePointSyncService, SharePointSyncService
from app.services.sharepoint_sync_queue_worker import SharePointSyncQueueWorker


def _build_graph_adapter_factory(settings):
    """Select the correct adapter factory based on SHAREPOINT_GRAPH_BACKEND."""
    backend = (settings.sharepoint.graph_backend or "httpx").strip().lower()
    if backend == "sdk":
        return MsgraphGraphAdapterFactory(settings)
    return HttpxGraphAdapterFactory(settings)


def build_sharepoint_providers(
    *,
    config,
    logger,
    blob_storage_service,
    sites_cosmos_repository,
    search_pipeline_orchestrator,
) -> tuple[
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
]:
    """Create provider objects for SharePoint sync and queue workflows."""

    graph_adapter_factory: providers.Singleton = providers.Singleton(
        _build_graph_adapter_factory,
        settings=config,
    )

    # Kept as singletons so external callers that already hold references
    # (e.g. queue worker, tests) don't break — they are no longer injected
    # into SharePointSyncService but remain available in the container tuple.
    sharepoint_membership_service: providers.Object = providers.Object(None)
    sharepoint_graph_client: providers.Object = providers.Object(None)
    sharepoint_site_discovery_service: providers.Object = providers.Object(None)

    sharepoint_sync_state_store: providers.Singleton[SharePointSyncStateStore] = providers.Singleton(
        SharePointSyncStateStore,
        blob_service=blob_storage_service,
        sites_repo=sites_cosmos_repository,
        build_state_id=providers.Object(SharePointSyncService._build_state_id),
        build_site_id=providers.Object(SharePointSyncService._build_site_id),
    )

    sharepoint_file_sync_runner: providers.Singleton[SharePointFileSyncRunner] = providers.Singleton(
        SharePointFileSyncRunner,
        logger=logger,
        build_state_id=providers.Object(SharePointSyncService._build_state_id),
    )

    sharepoint_transfer_service: providers.Singleton[SharePointTransferService] = providers.Singleton(
        SharePointTransferService,
        blob_service=blob_storage_service,
        graph_base_url=providers.Callable(lambda c: c.sharepoint.graph_base_url, config),
        download_chunk_size_bytes=providers.Callable(lambda c: c.sharepoint.download_chunk_size_bytes, config),
    )

    sharepoint_sync_service: providers.Singleton[ISharePointSyncService] = providers.Singleton(
        SharePointSyncService,
        settings=config,
        blob_service=blob_storage_service,
        logger=logger,
        sites_repo=sites_cosmos_repository,
        graph_adapter_factory=graph_adapter_factory,
        state_store=sharepoint_sync_state_store,
        file_sync_runner=sharepoint_file_sync_runner,
        transfer_service=sharepoint_transfer_service,
    )

    sharepoint_sync_queue_worker: providers.Singleton[SharePointSyncQueueWorker] = providers.Singleton(
        SharePointSyncQueueWorker,
        sync_service=sharepoint_sync_service,
        pipeline_orchestrator=search_pipeline_orchestrator,
        service_bus_settings=providers.Callable(lambda c: c.service_bus, config),
        logger=logger,
    )

    return (
        sharepoint_membership_service,
        sharepoint_graph_client,
        sharepoint_site_discovery_service,
        sharepoint_sync_state_store,
        sharepoint_file_sync_runner,
        sharepoint_transfer_service,
        sharepoint_sync_service,
        sharepoint_sync_queue_worker,
    )
