"""Ingestion Pipeline management endpoints.

This module provides FastAPI endpoints for managing the multimodal document
ingestion pipeline, including setup, indexing, and status monitoring.

Endpoints:
    POST   /pipeline/setup-pipeline    - Set up complete ingestion infrastructure
    POST   /pipeline/run-indexer       - Run the indexer to process documents
    GET    /pipeline/indexer-status    - Get indexer execution status

The pipeline includes:
- Blob storage data source with change detection
- Search index with vector search and semantic configuration
- Skillset with multimodal enrichment (text + image processing)
- Indexer for automatic document processing
"""

from typing import Any

from azure.core.exceptions import AzureError
from azure.search.documents.indexes.models import SearchIndexerStatus
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    get_indexer_service,
    get_logger,
    get_search_pipeline_orchestrator,
    get_settings,
    get_sharepoint_sync_service,
)
from app.api.schemas.pipeline import PipelineActionRequest
from app.api.schemas.sharepoint import SharePointSyncRequest
from app.core.settings import Settings
from app.ingestion.indexer_service import IIndexerService
from app.ingestion.search_pipeline_orchestrator import ISearchPipelineOrchestrator
from app.ingestion.sharepoint_sync_service import ISharePointSyncService

router = APIRouter(prefix="/pipeline", tags=["Ingestion Pipeline"])


def _serialize_indexer_issue(issue: Any) -> dict[str, Any]:
    """Serialize an indexer error/warning object into JSON-safe fields."""
    return {
        "key": getattr(issue, "key", None) or getattr(issue, "name", None) or getattr(issue, "document_key", None),
        "skill": getattr(issue, "name", None),
        "status_code": getattr(issue, "status_code", None),
        "message": getattr(issue, "error_message", None) or getattr(issue, "message", None) or str(issue),
        "details": getattr(issue, "details", None),
    }


def _serialize_execution_result(result: Any) -> dict[str, Any]:
    """Serialize indexer execution details, including per-item issues."""
    start_time = getattr(result, "start_time", None)
    end_time = getattr(result, "end_time", None)

    errors = [_serialize_indexer_issue(e) for e in (getattr(result, "errors", None) or [])]
    warnings = [_serialize_indexer_issue(w) for w in (getattr(result, "warnings", None) or [])]

    return {
        "status": getattr(result, "status", None),
        "error_message": getattr(result, "error_message", None),
        "start_time": start_time.isoformat() if start_time else None,
        "end_time": end_time.isoformat() if end_time else None,
        "items_processed": getattr(result, "item_count", None),
        "items_failed": getattr(result, "failed_item_count", None),
        "errors": errors,
        "warnings": warnings,
    }


def success(message: str, data: dict[str, Any] | None = None, status_code: int = 200) -> JSONResponse:
    """Create a standardised success JSON response.

    Args:
        message: Human-readable description of the outcome.
        data: Optional structured payload to include under the ``"data"`` key.
        status_code: HTTP status code (default ``200``).

    Returns:
        ``JSONResponse`` with ``{"message": ..., "data": ...}`` shape.
    """
    body: dict[str, Any] = {"message": message}
    if data:
        body["data"] = data
    return JSONResponse(status_code=status_code, content=body)


def error(status_code: int, message: str, details: str | None = None) -> JSONResponse:
    """Create a standardised error JSON response.

    Args:
        status_code: HTTP status code to return.
        message: Short human-readable error description.
        details: Optional extended detail string (e.g. exception message).

    Returns:
        ``JSONResponse`` with ``{"error": ..., "details": ...}`` shape.
    """
    body: dict[str, Any] = {"error": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status_code, content=body)


@router.post("/setup-pipeline")
async def setup_pipeline(
    request_body: PipelineActionRequest = Body(default=PipelineActionRequest()),
    orchestrator: ISearchPipelineOrchestrator = Depends(get_search_pipeline_orchestrator),
    indexer_service: IIndexerService = Depends(get_indexer_service),
    settings: Settings = Depends(get_settings),
    logger=Depends(get_logger),
) -> JSONResponse:
    """
    Set up the complete ingestion pipeline infrastructure.

    This endpoint orchestrates the creation of:
    - Blob storage data source with change detection
    - Search index with vector search and semantic configuration
    - Skillset with multimodal enrichment capabilities
    - Indexer to process documents

    Request Body (optional JSON):
    {
        "reset": false  // If true, resets indexer before setup
    }

    Returns:
        JSONResponse with pipeline setup status
    """
    logger.info("Setup pipeline endpoint triggered")

    try:
        reset_indexer: bool = request_body.reset

        # Set up complete pipeline
        logger.info("Starting pipeline setup...")
        await orchestrator.setup_pipeline_async()
        logger.info("Pipeline setup completed successfully")

        # Optionally reset indexer if requested
        if reset_indexer:
            logger.info("Resetting indexer as requested...")
            await indexer_service.reset_indexer_async(settings.search_service.indexer_name)
            logger.info("Indexer reset completed")

        return success(
            "Pipeline setup completed successfully",
            {"reset": reset_indexer},
        )

    except AzureError as e:
        logger.error(f"Azure error during pipeline setup: {str(e)}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error during pipeline setup: {str(e)}")
        return error(500, "Internal server error", str(e))


@router.post("/run-indexer")
async def run_indexer(
    request_body: PipelineActionRequest = Body(default=PipelineActionRequest()),
    indexer_name: str | None = None,
    indexer_service: IIndexerService = Depends(get_indexer_service),
    settings: Settings = Depends(get_settings),
    logger=Depends(get_logger),
) -> JSONResponse:
    """
    Run the search indexer to process documents.

    The indexer processes all documents in the configured blob storage
    container and updates the search index with enriched content.

    Query Parameters:
    - indexer_name (optional): Name of the indexer (defaults to configured name)

    Request Body (optional JSON):
    {
        "reset": false  // If true, resets indexer before running
    }

    Returns:
        JSONResponse with indexer run status
    """
    logger.info("Run indexer endpoint triggered")

    try:
        reset_first: bool = request_body.reset
        indexer_name = indexer_name or settings.search_service.indexer_name

        # Reset indexer if requested
        if reset_first:
            logger.info(f"Resetting indexer: {indexer_name}")
            await indexer_service.reset_indexer_async(indexer_name)
            logger.info("Indexer reset completed")

        # Run the indexer
        logger.info(f"Running indexer: {indexer_name}")
        await indexer_service.run_indexer_async(indexer_name)
        logger.info("Indexer run initiated successfully")

        return success(
            "Indexer run initiated successfully",
            {"indexer_name": indexer_name, "reset": reset_first},
        )

    except AzureError as e:
        logger.error(f"Azure error during indexer run: {str(e)}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error during indexer run: {str(e)}")
        return error(500, "Internal server error", str(e))


@router.get("/indexer-status")
async def get_indexer_status(
    indexer_name: str | None = None,
    indexer_service: IIndexerService = Depends(get_indexer_service),
    settings: Settings = Depends(get_settings),
    logger=Depends(get_logger),
) -> JSONResponse:
    """
    Get the current status of the indexer.

    Query Parameters:
    - indexer_name (optional): Name of the indexer (defaults to configured name)

    Returns:
        JSONResponse with indexer status information including:
        - Current status (running, idle, error, etc.)
        - Last execution results
        - Error/warning details if any
        - Item counts (processed, failed)
    """
    logger.info("Get indexer status endpoint triggered")

    try:
        # Get indexer name from query params or use default
        indexer_name = indexer_name or settings.search_service.indexer_name

        # Get indexer status
        logger.info(f"Retrieving status for indexer: {indexer_name}")
        status: SearchIndexerStatus = await indexer_service.get_indexer_status_async(indexer_name)

        # Extract detailed status information, including per-item issues.
        execution_history = list(getattr(status, "execution_history", None) or [])

        status_info: dict[str, Any] = {
            "indexer_name": status.name,
            "status": status.status,
            "last_result": _serialize_execution_result(status.last_result) if status.last_result else None,
            "execution_history": [_serialize_execution_result(item) for item in execution_history[:3]],
        }

        logger.info(f"Indexer status retrieved: {status.status}")
        return success(
            "Indexer status retrieved successfully",
            status_info,
        )

    except AzureError as e:
        logger.error(f"Azure error retrieving indexer status: {str(e)}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error retrieving indexer status: {str(e)}")
        return error(500, "Internal server error", str(e))


@router.post("/sync-sharepoint")
async def sync_sharepoint(
    request_body: SharePointSyncRequest = Body(...),
    sharepoint_service: ISharePointSyncService = Depends(get_sharepoint_sync_service),
    logger=Depends(get_logger),
) -> JSONResponse:
    """Copy files from a SharePoint document library into Blob Storage.

    Stages SharePoint content for downstream indexing. Identify the source
    via ``site_hostname`` + ``site_path`` and either ``library_name`` (drive
    display name) or ``drive_id``. Optional ``folder_path`` scopes the copy
    to a sub-folder.

    Returns:
        JSONResponse with a per-run summary (discovered/copied/skipped/failed)
        plus per-file outcomes.
    """
    logger.info("Sync SharePoint endpoint triggered")

    try:
        result = await sharepoint_service.sync(request_body)
        return success("SharePoint sync completed", result.model_dump())
    except ValueError as e:
        logger.warning(f"SharePoint sync rejected: {e}")
        return error(400, "Invalid SharePoint sync request", str(e))
    except AzureError as e:
        logger.error(f"Azure error during SharePoint sync: {e}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error during SharePoint sync: {e}")
        return error(500, "Internal server error", str(e))
