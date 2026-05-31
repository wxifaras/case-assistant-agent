"""Backward-compatible re-exports for provider group builders."""

from app.core.container_group_chat_workflow import build_chat_workflow_providers
from app.core.container_group_repositories import build_repository_providers
from app.core.container_group_search_ingestion import build_search_ingestion_providers
from app.core.container_group_sharepoint import build_sharepoint_providers

__all__ = [
    "build_repository_providers",
    "build_chat_workflow_providers",
    "build_search_ingestion_providers",
    "build_sharepoint_providers",
]
