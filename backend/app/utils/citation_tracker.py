"""Citation tracking utilities for source attribution.

Maintains source attribution throughout the retrieval pipeline.
"""

from app.core.logger import Logger
from app.models.chat import Citation, RetrievedDocument


class CitationTracker:
    """Tracks citations and maintains source attribution throughout the pipeline.

    Ensures all retrieved documents are properly indexed and can be referenced
    in generated answers. Uses the API ``Citation`` schema as the single source
    of truth.
    """

    def __init__(self, logger: Logger):
        """Initialize citation tracker.

        Args:
            logger: Injected logging service.
        """
        self.logger = logger
        # Keyed by content_id — the stable unique identifier for a document chunk.
        self.documents: dict[str, RetrievedDocument] = {}

    def add_documents(self, documents: list[RetrievedDocument]) -> None:
        """Add documents to the citation tracker, skipping already-tracked ones.

        Args:
            documents: List of retrieved documents to track.
        """
        for doc in documents:
            if doc.content_id not in self.documents:
                self.documents[doc.content_id] = doc
                self.logger.debug(f"Added document to tracker: {doc.content_id} ({doc.title!r:40})")

    def create_citations(self, documents: list[RetrievedDocument]) -> list[Citation]:
        """Create ``Citation`` objects for the provided documents.

        Typically called with vetted results from the reflection step so that
        only documents that actually contributed to the answer are surfaced.

        Args:
            documents: Documents to cite.

        Returns:
            List of ``Citation`` objects in the same order as *documents*.
        """
        return [
            Citation(
                document_id=doc.document_id,
                content_id=doc.content_id,
                content=doc.content,
                document_title=doc.title,
                page_number=doc.page_number,
            )
            for doc in documents
        ]

    def get_document_by_content_id(self, content_id: str) -> RetrievedDocument | None:
        """Look up a tracked document by its content ID.

        Args:
            content_id: The unique content-chunk identifier (matches ``RetrievedDocument.content_id``).

        Returns:
            The matching ``RetrievedDocument``, or ``None`` if not tracked.
        """
        return self.documents.get(content_id)

    def get_all_documents(self) -> list[RetrievedDocument]:
        """Return all currently tracked documents."""
        return list(self.documents.values())

    def get_document_count(self) -> int:
        """Return the total number of tracked documents."""
        return len(self.documents)
