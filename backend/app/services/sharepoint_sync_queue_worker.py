"""Background worker that consumes SharePoint sync requests from Service Bus.

This worker runs inside the FastAPI process and forwards queue messages to the
existing ``SharePointSyncService`` implementation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable

from azure.core.exceptions import HttpResponseError
from azure.identity.aio import DefaultAzureCredential
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus.exceptions import ServiceBusError
from pydantic import ValidationError

from app.api.schemas.sharepoint import SharePointSyncRequest
from app.core.logger import Logger
from app.core.settings import ServiceBusSettings
from app.ingestion.search.search_pipeline_orchestrator import ISearchPipelineOrchestrator
from app.ingestion.sharepoint.sharepoint_sync_service import ISharePointSyncService


class SharePointSyncQueueWorker:
    """Consume queued sync messages and execute SharePoint sync operations."""

    def __init__(
        self,
        sync_service: ISharePointSyncService,
        pipeline_orchestrator: ISearchPipelineOrchestrator,
        service_bus_settings: ServiceBusSettings,
        logger: Logger,
    ) -> None:
        self._sync_service = sync_service
        self._pipeline_orchestrator = pipeline_orchestrator
        self._service_bus = service_bus_settings
        self._logger = logger
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._pipeline_ready = False
        self._pipeline_setup_lock = asyncio.Lock()

    def is_enabled(self) -> bool:
        """Return True to keep queue consumption always enabled."""
        return True

    async def start(self) -> None:
        """Start the background consumer task."""

        if self._task and not self._task.done():
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="sharepoint-sync-queue-worker")
        self._logger.info("Service Bus sync queue consumer started.")

    async def stop(self) -> None:
        """Stop the background consumer task."""
        if not self._task:
            return

        self._stop_event.set()
        try:
            await self._task
        except Exception as exc:
            self._logger.warning(f"Service Bus sync queue consumer shutdown with error: {exc}")
        finally:
            self._task = None

    async def _run(self) -> None:
        queue_name = (self._service_bus.queue_name or "").strip()
        if not queue_name:
            self._logger.warning("SERVICEBUS_QUEUE_NAME is not set. Queue consumer not started.")
            return

        retry_delay_seconds = self._service_bus.receiver_retry_seconds
        max_delivery_attempts = self._service_bus.max_delivery_attempts

        while not self._stop_event.is_set():
            try:
                async with self._create_client() as client:
                    receiver = client.get_queue_receiver(queue_name=queue_name)
                    async with receiver:
                        while not self._stop_event.is_set():
                            messages = await receiver.receive_messages(max_message_count=10, max_wait_time=5)
                            if not messages:
                                continue

                            for message in messages:
                                await self._handle_message(
                                    receiver, message, max_delivery_attempts=max_delivery_attempts
                                )

            except (ServiceBusError, ValueError) as exc:
                self._logger.warning(f"Service Bus consumer loop error: {exc}. Retrying in {retry_delay_seconds}s")
                await asyncio.sleep(retry_delay_seconds)
            except Exception as exc:
                self._logger.error(f"Unexpected Service Bus consumer error: {exc}")
                await asyncio.sleep(retry_delay_seconds)

    def _create_client(self) -> ServiceBusClient:
        connection_string = (self._service_bus.connection_string or "").strip()
        if connection_string:
            return ServiceBusClient.from_connection_string(connection_string)

        fully_qualified_namespace = (self._service_bus.fqdn or "").strip()
        if not fully_qualified_namespace:
            raise ValueError("Set SERVICEBUS_CONNECTION_STRING or SERVICEBUS_FQDN.")

        managed_identity_client_id = (self._service_bus.managed_identity_client_id or "").strip() or None
        credential = DefaultAzureCredential(managed_identity_client_id=managed_identity_client_id)
        return ServiceBusClient(fully_qualified_namespace=fully_qualified_namespace, credential=credential)

    async def _handle_message(self, receiver, message, *, max_delivery_attempts: int) -> None:
        try:
            envelope = self._parse_message_body(message.body)
            payload = envelope.get("payload") if isinstance(envelope, dict) else envelope
            if not isinstance(payload, dict):
                raise ValueError("Message payload must be a JSON object.")

            sync_request = SharePointSyncRequest.model_validate(payload)
            sync_result = await self._sync_service.sync_site(sync_request)
            if self._has_indexable_changes(sync_result):
                await self._run_indexers_after_sync()
            else:
                self._logger.info(
                    "Skipping indexer trigger after SharePoint sync; no add/update/delete changes were detected."
                )
            await receiver.complete_message(message)

        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            self._logger.warning(f"Dead-lettering invalid sync queue message: {exc}")
            await receiver.dead_letter_message(message, reason="invalid-message", error_description=str(exc)[:1024])

        except Exception as exc:
            delivery_count = int(getattr(message, "delivery_count", 1) or 1)
            if delivery_count >= max_delivery_attempts:
                self._logger.warning(f"Dead-lettering failed sync queue message after {delivery_count} attempts: {exc}")
                await receiver.dead_letter_message(message, reason="sync-failed", error_description=str(exc)[:1024])
            else:
                self._logger.warning(f"Abandoning sync queue message (attempt {delivery_count}): {exc}")
                await receiver.abandon_message(message)

    @staticmethod
    def _parse_message_body(body: object) -> dict:
        if isinstance(body, str):
            return json.loads(body)

        if isinstance(body, bytes):
            return json.loads(body.decode("utf-8"))

        if isinstance(body, Iterable):
            content = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8") for chunk in body)
            return json.loads(content.decode("utf-8"))

        raise ValueError("Unsupported Service Bus message body type.")

    async def _run_indexers_after_sync(self) -> None:
        await self._ensure_pipeline_ready()

        try:
            await self._pipeline_orchestrator.run_indexer_async()
            self._logger.info("Triggered search indexers after SharePoint sync message.")
        except HttpResponseError as exc:
            if self._is_indexer_run_conflict(exc):
                self._logger.info("Indexer run already in progress; skipping duplicate trigger.")
                return
            raise

    async def _ensure_pipeline_ready(self) -> None:
        if self._pipeline_ready:
            return

        async with self._pipeline_setup_lock:
            if self._pipeline_ready:
                return

            if await self._pipeline_orchestrator.is_first_run_async():
                self._logger.info("Search pipeline not found. Running setup before indexer trigger.")
                await self._pipeline_orchestrator.setup_pipeline_async()

            self._pipeline_ready = True

    @staticmethod
    def _is_indexer_run_conflict(exc: HttpResponseError) -> bool:
        text = str(exc).lower()
        return "already in progress" in text or "already running" in text or "another indexer invocation" in text

    @staticmethod
    def _has_indexable_changes(sync_result: object) -> bool:
        added = int(getattr(sync_result, "added", 0) or 0)
        updated = int(getattr(sync_result, "updated", 0) or 0)
        deleted = int(getattr(sync_result, "deleted", 0) or 0)
        return (added + updated + deleted) > 0
