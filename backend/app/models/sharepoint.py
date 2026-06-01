"""Domain models for SharePoint site sync state stored in Cosmos DB."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class SharePointSiteItem(BaseModel):
    """Site metadata persisted per tenant/site."""

    id: str = Field(..., description="Unique item id in format tenant_id:site_id")
    tenant_id: str = Field(..., description="Tenant identifier (HPK level 1)")
    site_id: str = Field(..., description="SharePoint site id (HPK level 2)")
    doc_type: str = Field(default="site", description="Discriminator (HPK level 3)")
    site_hostname: str = Field(..., description="SharePoint hostname")
    site_path: str = Field(..., description="SharePoint site path")
    site_name: str = Field(default="", description="Resolved site display name")
    library_name: str = Field(default="", description="Resolved library name")
    drive_id: str = Field(default="", description="Resolved drive id")
    last_synced_utc: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last sync timestamp")


class SharePointSiteMemberItem(BaseModel):
    """Site member metadata persisted per tenant/site/member."""

    id: str = Field(..., description="Unique item id in format tenant_id:site_id:member_id")
    tenant_id: str = Field(..., description="Tenant identifier (HPK level 1)")
    site_id: str = Field(..., description="SharePoint site id (HPK level 2)")
    doc_type: str = Field(default="site_member", description="Discriminator (HPK level 3)")
    member_id: str = Field(..., description="AAD object id or principal id")
    display_name: str = Field(default="", description="Member display name")
    email: str = Field(default="", description="Member email address")
    role: str = Field(default="member", description="Normalized role: owner/member/visitor/unknown")
    source: str = Field(default="site-permissions", description="Origin of the resolved membership")
    last_seen_utc: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last seen timestamp")


class SharePointFileSyncStateItem(BaseModel):
    """Per-file sync state used for add/update/delete delta detection."""

    id: str = Field(..., description="Unique item id in format tenant_id:site_id:drive_id:item_id")
    tenant_id: str = Field(..., description="Tenant identifier (HPK level 1)")
    site_id: str = Field(..., description="SharePoint site id (HPK level 2)")
    doc_type: str = Field(default="sync_file_state", description="Discriminator (HPK level 3)")
    drive_id: str = Field(..., description="SharePoint drive id")
    item_id: str = Field(..., description="SharePoint drive item id")
    file_name: str = Field(default="", description="File name")
    relative_path: str = Field(default="", description="Path relative to selected folder scope")
    blob_name: str = Field(default="", description="Blob name in destination container")
    etag: str = Field(default="", description="SharePoint file eTag")
    last_modified_utc: str = Field(default="", description="SharePoint lastModifiedDateTime")
    size_bytes: int = Field(default=0, description="SharePoint file size")
    deleted: bool = Field(default=False, description="Whether file was reconciled as deleted")
    managed_by_sync: bool = Field(default=True, description="Safety marker for delete reconciliation")
    last_synced_utc: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last sync timestamp")
