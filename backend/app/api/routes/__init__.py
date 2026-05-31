"""API route modules for Case Assistant Agent.

This package contains all FastAPI route handlers organized by functionality:
- health: Application health and readiness probes
- pipeline: Document ingestion pipeline management endpoints

Each route module is registered with the main FastAPI app via APIRouter.
"""

from .health import router as health_router
from .pipeline import router as pipeline_router
from .sharepoint import router as sharepoint_sites_router

__all__ = ["health_router", "pipeline_router", "sharepoint_sites_router"]
