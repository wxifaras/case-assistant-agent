"""FastAPI application entry point for the Case Assistant Agent.

This module initializes the FastAPI application with routes, middleware,
and dependency injection for the Case Assistant Agent API.

Features:
    - FastAPI app with CORS middleware and error handling
    - Route modules: health, and document ingestion pipeline
    - Dependency injection container for service management
    - Pydantic settings with environment variable configuration
    - Application lifecycle management (startup/shutdown)

Routes:
    - /api/health: Health check and configuration validation
    - /api/pipeline: Document ingestion pipeline management

API Documentation:
    - Swagger UI: http://localhost:8000/docs
    - ReDoc: http://localhost:8000/redoc
    - OpenAPI JSON: http://localhost:8000/openapi.json
"""

import os
from contextlib import asynccontextmanager

from azure.cosmos import PartitionKey
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.routes import chat, health, pipeline
from app.core.container import Container

try:
    from azure.ai.projects.aio import AIProjectClient
    from azure.ai.projects.telemetry import AIProjectInstrumentor
    from azure.identity.aio import DefaultAzureCredential

    _FOUNDRY_AVAILABLE = True
except ImportError:
    _FOUNDRY_AVAILABLE = False

# Initialize dependency injection container (singleton)
# This container is shared across all route modules
container = Container()
logger = container.logger()

# Export container for use in route modules
__all__ = ["app", "container", "logger"]

_SECONDS_PER_DAY: int = 24 * 60 * 60


async def _configure_foundry_telemetry() -> None:
    """Enable Azure AI Foundry agent tracing.

    Fetches the Application Insights connection string from the Foundry project
    and activates ``AIProjectInstrumentor`` so MAF agent spans appear in the
    Foundry portal's Tracing view.
    """
    if not _FOUNDRY_AVAILABLE:
        logger.debug("azure-ai-projects not installed — skipping Foundry telemetry.")
        return

    project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        logger.debug("FOUNDRY_PROJECT_ENDPOINT not set — skipping Foundry telemetry.")
        return

    try:
        async with (
            DefaultAzureCredential() as credential,
            AIProjectClient(endpoint=project_endpoint, credential=credential) as project_client,
        ):
            conn_str = await project_client.telemetry.get_application_insights_connection_string()

        if conn_str:
            AIProjectInstrumentor().instrument(
                enable_content_recording=os.getenv("ENABLE_SENSITIVE_DATA", "false").lower() == "true"
            )
            logger.info(f"✓ Foundry agent telemetry configured from {project_endpoint}")
        else:
            logger.warning("Foundry project returned no Application Insights connection string.")

    except Exception as e:
        logger.warning(
            f"Failed to configure Foundry telemetry: {e}. "
            "MAF traces will still export via APPINSIGHTS_CONNECTION_STRING.",
            exc_info=True,
        )


async def _ensure_cosmos_resources() -> None:
    """Create Cosmos DB database and container if they don't exist.

    Called during FastAPI app startup to provision required Cosmos DB resources.
    """
    cosmos_options = container.cosmos_db_options()
    if not (cosmos_options.connection_string or cosmos_options.endpoint):
        logger.warning("Cosmos DB not configured - skipping setup")
        return

    try:
        logger.info("Ensuring Cosmos DB resources exist...")
        cosmos_client = container.cosmos_client()

        # Create database
        db = await cosmos_client.create_database_if_not_exists(id=cosmos_options.database_name)
        logger.info(f"[Cosmos] Database ready: {cosmos_options.database_name}")

        # Container properties
        container_kwargs = {}
        if getattr(cosmos_options, "enable_ttl", False):
            default_ttl_days = int(getattr(cosmos_options, "default_ttl_days", 30))
            container_kwargs["default_ttl"] = default_ttl_days * _SECONDS_PER_DAY

        # Indexing policy: exclude large serialized_message field
        indexing_policy = {
            "indexingMode": "consistent",
            "automatic": True,
            "includedPaths": [{"path": "/*"}],
            "excludedPaths": [{"path": "/serialized_message/?"}],
        }

        # Use Hierarchical Partition Key (HPK) for better scalability.
        # Paths must match the snake_case fields produced by ChatHistoryItem.model_dump().
        hpk = PartitionKey(path=["/user_id", "/session_id"], kind="MultiHash", version=2)

        await db.create_container_if_not_exists(
            id=cosmos_options.container_name,
            partition_key=hpk,
            indexing_policy=indexing_policy,
            **container_kwargs,
        )
        logger.info(f"[Cosmos] Container ready: {cosmos_options.container_name} (pk=[/user_id, /session_id] HPK)")

    except Exception as e:
        logger.error(f"Failed to provision Cosmos DB resources: {e}")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle (startup and shutdown).

    Args:
        app: FastAPI application instance.
    """
    # Startup
    logger.info("Agentic Case Assistant API starting up...")
    try:
        # Verify settings can be loaded
        settings = container.config()
        logger.info("Configuration loaded successfully:")
        logger.info(f"  Environment: {os.getenv('ENVIRONMENT', 'development')}")
        logger.info(f"  Search Service: {settings.search_service.endpoint}")
        logger.info(f"  Search Index: {settings.search_service.index_name}")
        logger.info(f"  Azure OpenAI: {settings.azure_openai.endpoint}")
        logger.info(f"  Chat Model Deployment Name: {settings.azure_openai.deployment_name}")
        logger.info(f"  Embedding Model Deployment Name: {settings.azure_openai.embedding_deployment_name}")
        logger.info(
            f"  Cosmos DB: {'Configured' if settings.cosmos_db.endpoint or settings.cosmos_db.connection_string else 'Not configured'}"
        )
        logger.info(f"  Blob Storage: {settings.blob_storage.resource_id}")
        logger.info(f"  Use Managed Identity: {settings.use_managed_identity}")

        # Provision Cosmos DB resources if they don't exist
        await _ensure_cosmos_resources()

        # Activate Foundry agent tracing (AIProjectInstrumentor)
        await _configure_foundry_telemetry()

    except Exception as e:
        logger.error(f"Failed to initialize application: {e}")
        raise

    yield
    # Shutdown`r`n    logger.info("Agentic Case Assistant API shutting down...")`r`n`r`n    # Close long-lived async clients owned by singleton services.`r`n    try:`r`n        pii_service = container.pii_detection_service()`r`n        await pii_service.close()`r`n    except Exception as e:`r`n        logger.warning(f"PII detection service cleanup failed: {e}")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured ``FastAPI`` application instance.
    """
    app = FastAPI(
        title="Agentic Case Assistant API",
        description="FastAPI backend for multimodal document search with Agentic Case Assistant.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure based on your security requirements
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health.router, prefix="/api")
    app.include_router(pipeline.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")

    # Redirect root to docs
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/docs")

    return app


# Create app instance
app = create_app()
