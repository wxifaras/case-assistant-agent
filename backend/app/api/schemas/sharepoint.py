"""Pydantic schemas for the SharePoint sync endpoint."""

from pydantic import BaseModel, Field


class SharePointSyncRequest(BaseModel):
    """Request payload for ``POST /pipeline/sync-sharepoint``.

    Identify the source by ``site_hostname`` + ``site_path`` (Graph
    ``/sites/{hostname}:{site-path}:`` resolution) and either ``library_name``
    (drive display name) or ``drive_id`` directly. Optional ``folder_path``
    scopes the copy to a sub-folder; otherwise the drive root is used.
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
    blob_prefix: str | None = Field(
        default=None,
        description="Optional blob name prefix. A trailing slash is added if missing.",
    )
    max_files: int | None = Field(
        default=None,
        ge=1,
        description="Per-request cap on files to copy. Bounded by SHAREPOINT_MAX_FILES_PER_RUN.",
    )
    dry_run: bool = Field(
        default=False,
        description="When True, list and report files without uploading to blob storage.",
    )


class SharePointSyncItemResult(BaseModel):
    """Per-file outcome of a sync run."""

    source_path: str = Field(..., description="SharePoint path of the source file")
    blob_name: str | None = Field(default=None, description="Destination blob name when uploaded")
    size_bytes: int | None = Field(default=None, description="File size reported by Graph")
    status: str = Field(..., description="One of: copied, skipped, failed, dry_run")
    reason: str | None = Field(default=None, description="Skip or failure reason when applicable")


class SharePointSyncResponse(BaseModel):
    """Aggregate response for a sync run."""

    discovered: int = Field(..., description="Total files discovered")
    copied: int = Field(..., description="Files successfully uploaded")
    skipped: int = Field(..., description="Files skipped (for example folders, capped, dry-run)")
    failed: int = Field(..., description="Files that failed during download or upload")
    elapsed_seconds: float = Field(..., description="Wall-clock duration of the sync run")
    destination_container: str = Field(..., description="Resolved destination blob container")
    items: list[SharePointSyncItemResult] = Field(
        default_factory=list, description="Per-file outcomes (truncated when very large)"
    )
    warnings: list[str] = Field(default_factory=list, description="Top-level warnings emitted during the run")
