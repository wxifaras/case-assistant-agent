"""Background worker that consumes SharePoint sync requests from Service Bus.

This worker runs inside the FastAPI process and forwards queue messages to the
existing ``SharePointSyncService`` implementation.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus.exceptions import ServiceBusError
from pydantic import ValidationError

from app.api.schemas.sharepoint import SharePointSyncRequest
from app.core.logger import Logger
from app.ingestion.sharepoint_sync_service import ISharePointSyncService


class SharePointSyncQueueWorker:
    """Consume queued sync messages and execute SharePoint sync operations."""

    def __init__(
        self,
        sync_service: ISharePointSyncService,
        logger: Logger,
    ) -> None:
        self._sync_service = sync_service
        self._logger = logger
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def is_enabled(self) -> bool:
        """Return True when queue consumption is enabled."""
        value = (os.getenv("SERVICEBUS_QUEUE_CONSUMER_ENABLED") or "false").strip().lower()
        return value in {"1", "true", "yes", "on"}

    async def start(self) -> None:
        """Start the background consumer task when enabled."""
        if not self.is_enabled():
            self._logger.info("Service Bus sync queue consumer disabled.")
            return

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
        queue_name = (os.getenv("SERVICEBUS_QUEUE_NAME") or "").strip()
        if not queue_name:
            self._logger.warning("SERVICEBUS_QUEUE_NAME is not set. Queue consumer not started.")
            return

        retry_delay_seconds = float((os.getenv("SERVICEBUS_RECEIVER_RETRY_SECONDS") or "5").strip() or "5")
        max_delivery_attempts = int((os.getenv("SERVICEBUS_MAX_DELIVERY_ATTEMPTS") or "5").strip() or "5")

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
        connection_string = (os.getenv("SERVICEBUS_CONNECTION_STRING") or "").strip()
        if connection_string:
            return ServiceBusClient.from_connection_string(connection_string)

        fully_qualified_namespace = (os.getenv("SERVICEBUS_FQDN") or "").strip()
        if not fully_qualified_namespace:
            raise ValueError("Set SERVICEBUS_CONNECTION_STRING or SERVICEBUS_FQDN.")

        managed_identity_client_id = (os.getenv("SERVICEBUS_MANAGED_IDENTITY_CLIENT_ID") or "").strip() or None
        credential = DefaultAzureCredential(managed_identity_client_id=managed_identity_client_id)
        return ServiceBusClient(fully_qualified_namespace=fully_qualified_namespace, credential=credential)

    async def _handle_message(self, receiver, message, *, max_delivery_attempts: int) -> None:
        try:
            envelope = self._parse_message_body(message.body)
            payload = envelope.get("payload") if isinstance(envelope, dict) else envelope
            if not isinstance(payload, dict):
                raise ValueError("Message payload must be a JSON object.")

            sync_request = SharePointSyncRequest.model_validate(payload)
            await self._sync_service.sync_site(sync_request)
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
