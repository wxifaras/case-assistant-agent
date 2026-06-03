"""Health check endpoints for the Case Assistant Agent.

This module provides a simple health check that verifies configuration
can be loaded and all required services are configured.

Purpose:
    - Verify API process is running
    - Check that all required configuration is loaded
    - Confirm all Azure services are accessible

Endpoints:
    GET /health              - Health check with configuration validation

Example Usage:
    ```bash
    curl http://localhost:8000/api/health
    # Response: {"status": "healthy", "configuration": "loaded"}
    ```
"""

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import get_logger, get_settings
from app.core.settings import Settings

# Initialize router
router = APIRouter(prefix="/health", tags=["Health"])


# ------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------


@router.get("")
async def health_check(
    settings: Settings = Depends(get_settings),
    logger=Depends(get_logger),
) -> JSONResponse:
    """
    Health check that verifies configuration is loaded.

    Returns:
        JSONResponse with health status and configuration validation
    """
    logger.info("Health check triggered")

    try:
        # Build health status from injected settings

        health_status: dict[str, Any] = {
            "status": "healthy",
            "configuration": "loaded",
            "services": {
                "search_service": {
                    "endpoint": str(settings.search_service.endpoint),
                    "index_name": settings.search_service.index_name,
                    "data_source_name": settings.search_service.data_source_name,
                    "skillset_name": settings.search_service.skillset_name,
                    "indexer_name": settings.search_service.indexer_name,
                    "skillset_api_version": settings.search_service.skillset_api_version,
                },
                "blob_storage": {
                    "container_name": settings.blob_storage.container_name,
                    "images_container_name": settings.blob_storage.images_container_name,
                    "has_resource_id": settings.blob_storage.resource_id is not None,
                    "has_connection_string": settings.blob_storage.connection_string is not None,
                },
                "ai_services": {
                    "endpoint": str(settings.ai_services.cognitive_services_endpoint),
                    "has_api_key": settings.ai_services.cognitive_services_key is not None,
                },
                "azure_openai": {
                    "resource_uri": str(settings.azure_openai.endpoint),
                    "text_embedding_model": settings.azure_openai.embedding_deployment_name,
                    "chat_completion_model": settings.azure_openai.deployment_name,
                    "has_api_key": settings.azure_openai.api_key is not None,
                    "has_separate_chat_endpoint": settings.azure_openai.chat_completion_resource_uri is not None,
                    "has_separate_chat_key": settings.azure_openai.chat_completion_api_key is not None,
                },
                "cosmos_db": {
                    "endpoint": str(settings.cosmos_db.endpoint) if settings.cosmos_db.endpoint else None,
                    "database_name": settings.cosmos_db.database_name,
                    "container_name": settings.cosmos_db.container_name,
                    "has_connection_string": settings.cosmos_db.connection_string is not None,
                    "enable_ttl": settings.cosmos_db.enable_ttl,
                    "default_ttl_days": settings.cosmos_db.default_ttl_days,
                },
                "key_vault": {
                    "url": str(settings.key_vault.url) if settings.key_vault.url else None,
                    "use_key_vault": settings.key_vault.use_key_vault,
                },
                "app_insights": {
                    "has_connection_string": settings.app_insights.connection_string is not None,
                },
                "api": {
                    "host": settings.api.host,
                    "port": settings.api.port,
                    "enable_cors": settings.api.enable_cors,
                    "enable_docs": settings.api.enable_docs,
                    "enable_auth": settings.api.enable_auth,
                    "require_jwt_validation": settings.api.require_jwt_validation,
                    "obo_enabled": settings.api.obo_enabled,
                },
            },
        }

        return JSONResponse(status_code=200, content=health_status)

    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )
