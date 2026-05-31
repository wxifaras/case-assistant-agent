"""Pydantic schemas for the SharePoint sync endpoint."""

from pydantic import BaseModel, Field


class SharePointSyncRequest(BaseModel):
    """Request payload for ``POST /sharepoint/sites/sync-site``.

    Identify the source by ``site_hostname`` + ``site_path`` (Graph
    ``/sites/{hostname}:{site-path}:`` resolution) and either ``library_name``
    (drive display name) or ``drive_id`` directly. Optional ``folder_path``
    scopes the copy to a sub-folder; otherwise the drive root is used.

    When omitted, ``site_hostname``, ``site_path``, and ``library_name`` can
    fall back to configured SharePoint defaults where available.
    """

    site_hostname: str | None = Field(
        default=None,
        min_length=1,
        description="SharePoint hostname. Optional when SHAREPOINT_SITE_HOSTNAME is configured.",
    )
    site_path: str | None = Field(
        default=None,
        min_length=1,
        description="Site server-relative path. Optional when SHAREPOINT_SITE_PATH is configured.",
    )
    library_name: str | None = Field(
        default=None,
        description="Document library display name (drive). Required if drive_id and SHAREPOINT_LIBRARY_NAME are not set.",
    )
    drive_id: str | None = Field(
        default=None,
        description="Graph drive id. Takes precedence over library_name when both are provided.",
    )
    folder_path: str | None = Field(
        default=None,
        description="Optional folder path within the library, for example 'Cases/2026'. Root used when omitted.",
    )
    destination_container: str | None = Field(
        default=None,
        description="Destination blob container. Falls back to SHAREPOINT_DEFAULT_BLOB_CONTAINER or BLOBSTORAGE_CONTAINER_NAME.",
    )
    tenant_id: str | None = Field(
        default=None,
        min_length=1,
        description="Tenant identifier used for Cosmos partitioning. Defaults to AZURE_TENANT_ID when omitted.",
    )


class SharePointSyncItemResult(BaseModel):
    """Per-file outcome of a sync run."""

    source_path: str = Field(..., description="SharePoint path of the source file")
    blob_name: str | None = Field(default=None, description="Destination blob name when uploaded")
    size_bytes: int | None = Field(default=None, description="File size reported by Graph")
    status: str = Field(..., description="One of: copied, skipped, failed")
    reason: str | None = Field(default=None, description="Skip or failure reason when applicable")


class SharePointSyncResponse(BaseModel):
    """Aggregate response for a sync run."""

    discovered: int = Field(..., description="Total files discovered")
    copied: int = Field(..., description="Files successfully uploaded")
    skipped: int = Field(..., description="Files skipped (for example folders, unchanged)")
    failed: int = Field(..., description="Files that failed during download or upload")
    elapsed_seconds: float = Field(..., description="Wall-clock duration of the sync run")
    destination_container: str = Field(..., description="Resolved destination blob container")
    items: list[SharePointSyncItemResult] = Field(
        default_factory=list, description="Per-file outcomes (truncated when very large)"
    )
    warnings: list[str] = Field(default_factory=list, description="Top-level warnings emitted during the run")
    added: int = Field(default=0, description="Files detected as new during this run")
    updated: int = Field(default=0, description="Files detected as updated during this run")
    unchanged: int = Field(default=0, description="Files unchanged from previous sync state")
    deleted: int = Field(default=0, description="Files reconciled as deleted and soft-marked in blob metadata")


class SharePointMultiSiteSyncRequest(BaseModel):
    """Payload for syncing multiple sites in one API call."""

    tenant_id: str | None = Field(
        default=None,
        min_length=1,
        description="Tenant identifier for partitioning. Defaults to AZURE_TENANT_ID when omitted.",
    )
    sites: list[SharePointSyncRequest] = Field(default_factory=list, description="Per-site sync requests")


class SharePointMultiSiteSyncResponse(BaseModel):
    """Aggregate payload for multi-site sync."""

    tenant_id: str = Field(..., description="Tenant identifier used for this run")
    total_sites: int = Field(default=0, description="Total sites requested")
    succeeded_sites: int = Field(default=0, description="Sites synced without fatal error")
    failed_sites: int = Field(default=0, description="Sites that failed")
    results: list[SharePointSyncResponse] = Field(default_factory=list, description="Per-site sync results")
    errors: list[str] = Field(default_factory=list, description="Per-site error summaries")
