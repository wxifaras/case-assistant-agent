from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.settings import ServiceBusSettings
from app.services.sharepoint_sync_queue_worker import SharePointSyncQueueWorker


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_skips_indexer_when_no_indexable_changes() -> None:
    sync_service = MagicMock()
    sync_service.sync_site = AsyncMock(
        return_value=SimpleNamespace(
            added=0,
            updated=0,
            deleted=0,
            skipped=5,
            unchanged=5,
        )
    )
    pipeline_orchestrator = MagicMock()
    logger = MagicMock()

    worker = SharePointSyncQueueWorker(
        sync_service=sync_service,
        pipeline_orchestrator=pipeline_orchestrator,
        service_bus_settings=cast(ServiceBusSettings, SimpleNamespace()),
        logger=logger,
    )
    worker._run_indexers_after_sync = AsyncMock()  # type: ignore[method-assign]

    receiver = AsyncMock()
    message = SimpleNamespace(
        body=b'{"payload": {"site_hostname": "contoso.sharepoint.com", "site_path": "/sites/MySite", "library_name": "Documents"}}',
        delivery_count=1,
    )

    await worker._handle_message(receiver, message, max_delivery_attempts=5)

    sync_service.sync_site.assert_awaited_once()
    worker._run_indexers_after_sync.assert_not_awaited()  # type: ignore[attr-defined]
    receiver.complete_message.assert_awaited_once_with(message)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_triggers_indexer_when_changes_detected() -> None:
    sync_service = MagicMock()
    sync_service.sync_site = AsyncMock(
        return_value=SimpleNamespace(
            added=1,
            updated=0,
            deleted=0,
            skipped=4,
            unchanged=4,
        )
    )
    pipeline_orchestrator = MagicMock()
    logger = MagicMock()

    worker = SharePointSyncQueueWorker(
        sync_service=sync_service,
        pipeline_orchestrator=pipeline_orchestrator,
        service_bus_settings=cast(ServiceBusSettings, SimpleNamespace()),
        logger=logger,
    )
    worker._run_indexers_after_sync = AsyncMock()  # type: ignore[method-assign]

    receiver = AsyncMock()
    message = SimpleNamespace(
        body=b'{"payload": {"site_hostname": "contoso.sharepoint.com", "site_path": "/sites/MySite", "library_name": "Documents"}}',
        delivery_count=1,
    )

    await worker._handle_message(receiver, message, max_delivery_attempts=5)

    worker._run_indexers_after_sync.assert_awaited_once()  # type: ignore[attr-defined]
    receiver.complete_message.assert_awaited_once_with(message)
