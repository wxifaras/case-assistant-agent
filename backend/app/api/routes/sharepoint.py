"""SharePoint site discovery and multi-site sync endpoints."""

from typing import Any

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.api.dependencies import get_logger, get_sharepoint_sync_service
from app.api.schemas.sharepoint import SharePointMultiSiteSyncRequest, SharePointSyncRequest
from app.ingestion.sharepoint_sync_service import ISharePointSyncService

router = APIRouter(prefix="/sharepoint/sites", tags=["SharePoint Sites"], redirect_slashes=False)


def success(message: str, data: dict[str, Any] | None = None, status_code: int = 200) -> JSONResponse:
    body: dict[str, Any] = {"message": message}
    if data:
        body["data"] = data
    return JSONResponse(status_code=status_code, content=body)


def error(status_code: int, message: str, details: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status_code, content=body)


@router.get("")
async def get_sites(
    search: str = Query(default="*", min_length=1, description="Graph site search string."),
    max_results: int = Query(default=200, ge=1, le=1000, description="Maximum number of sites to return."),
    sharepoint_service: ISharePointSyncService = Depends(get_sharepoint_sync_service),
    logger=Depends(get_logger),
) -> JSONResponse:
    """List SharePoint sites visible to the configured app identity."""
    logger.info(f"SharePoint list sites endpoint triggered: search='{search}' max_results={max_results}")
    try:
        sites = await sharepoint_service.get_sites(search=search, max_results=max_results)
        return success(
            "SharePoint sites retrieved",
            {
                "search": search,
                "count": len(sites),
                "sites": sites,
            },
        )
    except ValueError as e:
        logger.warning(f"Invalid list sites request: {e}")
        return error(400, "Invalid list sites request", str(e))
    except AzureError as e:
        logger.error(f"Azure error during list sites: {e}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error during list sites: {e}")
        return error(500, "Internal server error", str(e))


@router.get("/member-of")
async def get_member_sites(
    user_id: str = Query(..., min_length=1, description="User object id or email to resolve site memberships."),
    search: str = Query(default="*", min_length=1, description="Optional Graph site search filter."),
    max_results: int = Query(
        default=200, ge=1, le=1000, description="Maximum number of candidate sites for graph source."
    ),
    tenant_id: str | None = Query(
        default=None,
        min_length=1,
        description="Tenant identifier. Defaults to AZURE_TENANT_ID when omitted.",
    ),
    sharepoint_service: ISharePointSyncService = Depends(get_sharepoint_sync_service),
    logger=Depends(get_logger),
) -> JSONResponse:
    """List sites where the specified user has membership from Graph."""
    logger.info("SharePoint member-of endpoint triggered")
    try:
        memberships = await sharepoint_service.get_member_sites(
            user_id=user_id,
            search=search,
            max_results=max_results,
            tenant_id=tenant_id,
        )
        return success(
            "SharePoint member sites retrieved",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "search": search,
                "count": len(memberships),
                "sites": memberships,
            },
        )
    except ValueError as e:
        logger.warning(f"Invalid member-of request: {e}")
        return error(400, "Invalid member-of request", str(e))
    except AzureError as e:
        logger.error(f"Azure error during member-of query: {e}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error during member-of query: {e}")
        return error(500, "Internal server error", str(e))


@router.get("/members")
async def get_site_members(
    site_hostname: str = Query(..., min_length=1, description="SharePoint hostname for the site."),
    site_path: str = Query(..., min_length=1, description="SharePoint site path, for example /sites/MySite."),
    tenant_id: str | None = Query(
        default=None,
        min_length=1,
        description="Tenant identifier. Defaults to AZURE_TENANT_ID when omitted.",
    ),
    sharepoint_service: ISharePointSyncService = Depends(get_sharepoint_sync_service),
    logger=Depends(get_logger),
) -> JSONResponse:
    """List real-time site members from Graph for the requested site."""
    logger.info("SharePoint members endpoint triggered")
    try:
        members = await sharepoint_service.get_site_members(
            site_hostname=site_hostname,
            site_path=site_path,
            tenant_id=tenant_id,
        )
        return success(
            "SharePoint site members retrieved",
            {
                "site_hostname": site_hostname,
                "site_path": site_path,
                "tenant_id": tenant_id,
                "count": len(members),
                "members": members,
            },
        )
    except ValueError as e:
        logger.warning(f"Invalid members request: {e}")
        return error(400, "Invalid members request", str(e))
    except AzureError as e:
        logger.error(f"Azure error during members query: {e}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error during members query: {e}")
        return error(500, "Internal server error", str(e))


@router.post("/sync-site")
async def sync_site(
    request_body: SharePointSyncRequest = Body(...),
    sharepoint_service: ISharePointSyncService = Depends(get_sharepoint_sync_service),
    logger=Depends(get_logger),
) -> JSONResponse:
    """Sync one SharePoint site into Blob storage.

    This is the canonical single-site SharePoint sync route.
    """
    logger.info("SharePoint single-site sync endpoint triggered")
    try:
        result = await sharepoint_service.sync_site(request_body)
        return success("SharePoint sync completed", result.model_dump())
    except ValueError as e:
        logger.warning(f"Invalid single-site sync request: {e}")
        return error(400, "Invalid SharePoint sync request", str(e))
    except AzureError as e:
        logger.error(f"Azure error during single-site sync: {e}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error during single-site sync: {e}")
        return error(500, "Internal server error", str(e))


@router.post("/sync")
async def sync(
    request_body: SharePointMultiSiteSyncRequest = Body(...),
    sharepoint_service: ISharePointSyncService = Depends(get_sharepoint_sync_service),
    logger=Depends(get_logger),
) -> JSONResponse:
    """Sync multiple SharePoint sites sequentially."""
    logger.info("SharePoint multi-site sync endpoint triggered")
    try:
        result = await sharepoint_service.sync(request_body)
        return success("SharePoint multi-site sync completed", result.model_dump())
    except ValueError as e:
        logger.warning(f"Invalid multi-site sync request: {e}")
        return error(400, "Invalid multi-site sync request", str(e))
    except AzureError as e:
        logger.error(f"Azure error during multi-site sync: {e}")
        return error(500, "Azure service error", str(e))
    except Exception as e:
        logger.error(f"Unexpected error during multi-site sync: {e}")
        return error(500, "Internal server error", str(e))
