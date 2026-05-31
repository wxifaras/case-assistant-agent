import json
import logging
import os
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

app = func.FunctionApp()


class SchedulerSettings(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    execution_mode: Literal["api", "queue"] = "api"
    default_tenant_id: str = Field(default="default", min_length=1)
    api_base_url: str = "http://localhost:8000/api"
    api_sync_path: str = "/sharepoint/sites/sync"
    api_search: str = "*"
    api_max_results: int = Field(default=200, ge=1, le=1000)
    api_timeout_seconds: int = Field(default=30, ge=5)
    default_library_name: str = "Documents"

    @field_validator("execution_mode", mode="before")
    @classmethod
    def _normalize_execution_mode(cls, value: object) -> object:
        return str(value or "api").strip().lower()

    @field_validator("api_sync_path", mode="before")
    @classmethod
    def _normalize_sync_path(cls, value: object) -> str:
        path = str(value or "/sharepoint/sites/sync").strip()
        if not path:
            return "/sharepoint/sites/sync"
        return path if path.startswith("/") else f"/{path}"

    @field_validator("api_base_url", mode="before")
    @classmethod
    def _normalize_api_base_url(cls, value: object) -> str:
        url = str(value or "http://localhost:8000/api").strip().rstrip("/")
        return url or "http://localhost:8000/api"

    @field_validator("api_search", mode="before")
    @classmethod
    def _normalize_api_search(cls, value: object) -> str:
        search = str(value or "*").strip()
        return search or "*"

    @field_validator("default_library_name", mode="before")
    @classmethod
    def _normalize_library(cls, value: object) -> str:
        name = str(value or "Documents").strip()
        return name or "Documents"

    @classmethod
    def from_env(cls) -> "SchedulerSettings":
        raw = {
            "default_tenant_id": os.getenv("SYNC_DEFAULT_TENANT_ID"),
            "api_base_url": os.getenv("SYNC_API_BASE_URL"),
            "api_sync_path": os.getenv("SYNC_API_SYNC_PATH"),
            "api_search": os.getenv("SYNC_SITES_SEARCH"),
            "api_max_results": os.getenv("SYNC_SITES_MAX_RESULTS"),
            "api_timeout_seconds": os.getenv("SYNC_API_TIMEOUT_SECONDS"),
            "default_library_name": os.getenv("SYNC_DEFAULT_LIBRARY_NAME"),
            "execution_mode": os.getenv("SYNC_EXECUTION_MODE"),
        }
        env_values = {k: v for k, v in raw.items() if v is not None}
        return cls.model_validate(env_values)


def _service_bus_client() -> ServiceBusClient:
    connection_string = (os.getenv("SERVICEBUS_CONNECTION_STRING") or "").strip()
    if connection_string:
        return ServiceBusClient.from_connection_string(connection_string)

    fully_qualified_namespace = (os.getenv("SERVICEBUS_FQDN") or "").strip()
    if not fully_qualified_namespace:
        raise ValueError("Set SERVICEBUS_CONNECTION_STRING or SERVICEBUS_FQDN.")

    managed_identity_client_id = (os.getenv("SERVICEBUS_MANAGED_IDENTITY_CLIENT_ID") or "").strip() or None
    credential = DefaultAzureCredential(managed_identity_client_id=managed_identity_client_id)
    return ServiceBusClient(fully_qualified_namespace=fully_qualified_namespace, credential=credential)


def _queue_name() -> str:
    queue_name = (os.getenv("SERVICEBUS_QUEUE_NAME") or "").strip()
    if not queue_name:
        raise ValueError("SERVICEBUS_QUEUE_NAME is required.")
    return queue_name


def _normalize_site_payload(raw: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    payload = {
        "tenant_id": tenant_id,
        "site_hostname": raw.get("site_hostname"),
        "site_path": raw.get("site_path"),
        "library_name": raw.get("library_name"),
        "drive_id": raw.get("drive_id"),
        "folder_path": raw.get("folder_path"),
        "destination_container": raw.get("destination_container"),
        "max_files": raw.get("max_files"),
    }
    return {k: v for k, v in payload.items() if v is not None}


def _build_messages(sites: list[dict[str, Any]], tenant_id: str, source: str) -> list[dict[str, Any]]:
    sites = _validate_sites_or_raise(sites)
    now = datetime.now(UTC).isoformat()
    messages: list[dict[str, Any]] = []
    for site in sites:
        messages.append(
            {
                "message_type": "sharepoint_sync_request",
                "source": source,
                "enqueued_at_utc": now,
                "payload": _normalize_site_payload(site, tenant_id),
            }
        )
    return messages


def _has_required_site_target(site: dict[str, Any]) -> bool:
    site_hostname = str(site.get("site_hostname") or "").strip()
    site_path = str(site.get("site_path") or "").strip()
    return bool(site_hostname and site_path)


def _validate_sites_or_raise(sites: Any) -> list[dict[str, Any]]:
    if not isinstance(sites, list) or not all(isinstance(site, dict) for site in sites):
        raise ValueError("'sites' must be a list of objects.")

    invalid_indexes = [index for index, site in enumerate(sites) if not _has_required_site_target(site)]
    if invalid_indexes:
        raise ValueError(
            "Each site entry must include non-empty 'site_hostname' and 'site_path'. "
            f"Invalid entries at indexes: {invalid_indexes}"
        )

    return sites


def _enqueue_messages(message_payloads: list[dict[str, Any]]) -> int:
    if not message_payloads:
        return 0

    queue_name = _queue_name()
    with _service_bus_client() as client:
        with client.get_queue_sender(queue_name=queue_name) as sender:
            batch = sender.create_message_batch()
            sent = 0
            for payload in message_payloads:
                body = json.dumps(payload)
                message = ServiceBusMessage(body, content_type="application/json")
                try:
                    batch.add_message(message)
                except ValueError:
                    sender.send_messages(batch)
                    sent += len(batch)
                    batch = sender.create_message_batch()
                    try:
                        batch.add_message(message)
                    except ValueError as exc:
                        raise ValueError("Single message exceeds Service Bus max message size.") from exc
            if len(batch) > 0:
                sender.send_messages(batch)
                sent += len(batch)

    return sent


def _sync_sites_via_api(settings: SchedulerSettings, tenant_id: str, sites: list[dict[str, Any]]) -> dict[str, Any]:
    sites = _validate_sites_or_raise(sites)
    payload = {
        "tenant_id": tenant_id,
        "sites": [_normalize_site_payload(site, tenant_id) for site in sites],
    }
    request = Request(
        url=f"{settings.api_base_url}{settings.api_sync_path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.api_timeout_seconds) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Sync API returned HTTP {exc.code}: {details[:500]}") from exc


def _site_from_web_url(web_url: str) -> dict[str, Any] | None:
    parsed = urlparse(web_url)
    hostname = (parsed.hostname or "").strip()
    path = (parsed.path or "/").strip()
    if not hostname or not path:
        return None
    return {
        "site_hostname": hostname,
        "site_path": path,
    }


def _load_scheduled_sites(settings: SchedulerSettings) -> tuple[str, list[dict[str, Any]]]:
    tenant_id = settings.default_tenant_id
    query = urlencode({"search": settings.api_search, "max_results": settings.api_max_results})
    request = Request(
        url=f"{settings.api_base_url}/sharepoint/sites?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urlopen(
        request, timeout=settings.api_timeout_seconds
    ) as response:  # nosec B310 - endpoint is explicitly configured
        payload = json.loads(response.read().decode("utf-8"))

    data = payload.get("data") if isinstance(payload, dict) else {}
    source_sites = data.get("sites") if isinstance(data, dict) else []
    if not isinstance(source_sites, list):
        return tenant_id, []

    default_library = settings.default_library_name
    sites: list[dict[str, Any]] = []
    for item in source_sites:
        if not isinstance(item, dict):
            continue
        web_url = str(item.get("webUrl") or item.get("web_url") or "").strip()
        site = _site_from_web_url(web_url)
        if site is None:
            continue
        site["library_name"] = default_library
        sites.append(
            {
                "tenant_id": tenant_id,
                "site_hostname": site["site_hostname"],
                "site_path": site["site_path"],
                "library_name": site["library_name"],
            }
        )

    return tenant_id, sites


@app.function_name(name="ScheduleSharePointSync")
@app.timer_trigger(schedule="%SHAREPOINT_SYNC_SCHEDULE%", arg_name="timer", run_on_startup=False, use_monitor=True)
def schedule_sharepoint_sync(timer: func.TimerRequest) -> None:
    logging.info("SharePoint sync scheduler timer fired.")
    try:
        settings = SchedulerSettings.from_env()
        tenant_id, sites = _load_scheduled_sites(settings)
        if not sites:
            logging.info("No sites resolved for scheduled sync; nothing to do.")
            return

        sites = _validate_sites_or_raise(sites)

        if settings.execution_mode == "api":
            logging.info("Executing scheduled sync via API (%s site(s)).", len(sites))
            result = _sync_sites_via_api(settings, tenant_id=tenant_id, sites=sites)
            data = result.get("data") if isinstance(result, dict) else {}
            if isinstance(data, dict):
                logging.info(
                    "Scheduled API sync done: total=%s succeeded=%s failed=%s",
                    data.get("total_sites"),
                    data.get("succeeded_sites"),
                    data.get("failed_sites"),
                )
            else:
                logging.info("Scheduled API sync completed.")
            return

        payloads = _build_messages(sites, tenant_id=tenant_id, source="timer")
        sent = _enqueue_messages(payloads)
        logging.info("Queued %s SharePoint sync message(s).", sent)
    except Exception as exc:
        logging.exception("Failed to schedule SharePoint sync messages: %s", exc)
        raise


@app.function_name(name="EnqueueSharePointSync")
@app.route(route="schedule/sharepoint-sync", auth_level=func.AuthLevel.FUNCTION, methods=["POST"])
def enqueue_sharepoint_sync(req: func.HttpRequest) -> func.HttpResponse:
    settings = SchedulerSettings.from_env()

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body.", status_code=400)

    if not isinstance(body, dict):
        return func.HttpResponse("Request body must be a JSON object.", status_code=400)

    tenant_id = str(body.get("tenant_id") or settings.default_tenant_id)
    sites = body.get("sites")

    if sites is None:
        # Allow single-site payload shape for convenience.
        sites = [body]

    try:
        sites = _validate_sites_or_raise(sites)
    except ValueError as exc:
        return func.HttpResponse(str(exc), status_code=400)

    if settings.execution_mode == "api":
        try:
            result = _sync_sites_via_api(settings, tenant_id=tenant_id, sites=sites)
            response = {
                "execution_mode": "api",
                "tenant_id": tenant_id,
                "requested_sites": len(sites),
                "result": result,
            }
            return func.HttpResponse(json.dumps(response), mimetype="application/json", status_code=200)
        except Exception as exc:
            logging.exception("Failed HTTP-triggered API sync: %s", exc)
            return func.HttpResponse(str(exc), status_code=500)

    payloads = _build_messages(sites, tenant_id=tenant_id, source="http")
    sent = _enqueue_messages(payloads)

    response = {
        "execution_mode": "queue",
        "tenant_id": tenant_id,
        "queued_messages": sent,
        "queue_name": _queue_name(),
    }
    return func.HttpResponse(json.dumps(response), mimetype="application/json", status_code=202)
