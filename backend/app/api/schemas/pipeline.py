"""Pydantic schemas for pipeline endpoints.

This module defines request models for the ingestion pipeline API endpoints.
Response models use JSONResponse directly for flexibility.
"""

from pydantic import BaseModel, Field


class PipelineActionRequest(BaseModel):
    """Request model for pipeline actions that support reset option.

    Attributes:
        reset: If true, resets the indexer before performing the action.
    """

    reset: bool = Field(
        default=False,
        description="If true, resets the indexer before performing the action",
    )
