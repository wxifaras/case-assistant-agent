"""Document ingestion package.

This package groups ingestion modules by domain:
- ``search`` for Azure AI Search ingestion pipeline services
- ``sharepoint`` for SharePoint discovery, sync orchestration, and file sync helpers
"""

from . import search, sharepoint

__all__ = [
    "search",
    "sharepoint",
]
