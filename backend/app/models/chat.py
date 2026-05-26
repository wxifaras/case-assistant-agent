"""Domain models for chat, RAG workflows, and conversation history.

This module contains all Pydantic models related to chat functionality,
including agentic RAG workflow state, document retrieval, and conversation storage.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Source document citation with metadata.

    Used throughout the application for tracking source attribution
    in generated responses and workflow execution.

    Attributes:
        document_id: Unique identifier for the source document (e.g., "doc-123-abc")
        content_id: Unique identifier for the content chunk (e.g., "content-123-abc")
        content: The actual text content of the citation.
        document_title: Title of the source document.
        page_number: Page number where the content appears.
    """

    document_id: str = Field(..., description="Unique identifier for the source document")
    content_id: str = Field(..., description="Unique identifier for the content chunk")
    content: str | None = Field(default=None, description="The actual text content of the cited chunk")
    document_title: str | None = Field(default=None, description="Title of the source document")
    page_number: int | None = Field(default=None, description="Page number where the content appears")


class RetrievedDocument(BaseModel):
    """Document retrieved from Azure AI Search with relevance scores and metadata."""

    document_id: str = Field(..., description="Unique identifier for the source document")
    content_id: str = Field(..., description="Unique identifier for the content chunk")
    title: str = Field(..., description="Document title")
    content: str = Field(..., description="Text content of the retrieved chunk")
    source: str = Field(..., description="Source path or URL of the document")
    page_number: int | None = Field(default=None, description="Page number of the chunk")
    score: float = Field(..., description="Hybrid search relevance score")
    reranker_score: float | None = Field(default=None, description="Semantic reranker score (if enabled)")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional document metadata")


class ChatHistoryItem(BaseModel):
    """Cosmos DB chat history item.

    Represents a single message in conversation history stored in Cosmos DB.
    Uses Hierarchical Partition Key (HPK) with user_id and session_id for efficient querying.

    Attributes:
        id: Unique item ID in format {user_id}_{session_id}_{message_id}
        user_id: User identifier (partition key level 1)
        session_id: Session identifier (partition key level 2)
        timestamp: Message timestamp in UTC
        serialized_message: JSON-serialized message data
        message_text: Plain text of the message for quick access
        message_id: Unique message identifier
        role: Message role (user, assistant, system)
    """

    id: str = Field(..., description="Unique item ID")
    user_id: str = Field(..., description="User identifier (partition key level 1)")
    session_id: str = Field(..., description="Session identifier (partition key level 2)")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Message timestamp")
    serialized_message: str = Field(..., description="JSON-serialized message")
    message_text: str | None = Field(default=None, description="Message text")
    message_id: str = Field(..., description="Unique message ID")
    role: str = Field(..., description="Message role")
    doc_type: str = Field(
        default="chat_message",
        description=(
            "Discriminator used to distinguish chat-history docs from other doc types "
            "(e.g. 'workflow_checkpoint') stored in the same Cosmos container."
        ),
    )


class RewrittenQuery(BaseModel):
    """Structured output of the ``QueryRewriterAgent`` HyDE step."""

    hypothetical_passage: str = Field(
        ..., description="Hypothetical document passage generated to represent expected answer content"
    )
    reasoning: str = Field(..., description="Agent's explanation of the rewriting strategy chosen")


class GeneratedAnswer(BaseModel):
    """Answer produced by the ``AnswerGenerator`` agent with inline citations."""

    answer_text: str = Field(..., description="Generated answer text with inline [n] citation markers")
    citations: list[Citation] = Field(default_factory=list, description="Ordered list of cited sources")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional generation metadata")


class ReviewDecision(BaseModel):
    """LLM decision for reviewing search results.

    Used by the reflection agent to classify search results as valid or invalid
    and decide whether to continue searching or finalise.
    """

    thought_process: str = Field(..., description="Agent's reasoning for the decision")
    valid_results: list[int] = Field(..., description="Indices of valid results in the current result set")
    invalid_results: list[int] = Field(..., description="Indices of invalid results in the current result set")
    decision: Literal["retry", "finalize"] = Field(..., description="Whether to retry retrieval or finalise")


class AgenticRAGState(BaseModel):
    """State for agentic RAG workflow.

    Tracks the complete state through search → reflection → answer generation cycles.
    The thought_process captures each step with detailed metadata for observability.
    """

    query: str
    user_id: str | None = None
    session_id: str | None = None
    chat_history: list[dict[str, str]] | None = Field(default_factory=list)
    filters: dict[str, Any] | None = None

    # Iteration control
    max_attempts: int = 3
    current_attempt: int = 0
    search_history: list[dict[str, Any]] = Field(default_factory=list)
    previous_reviews: list[str] = Field(default_factory=list)  # Review text from LLM
    decisions: list[str] = Field(default_factory=list)  # Track decision per iteration

    # Results tracking
    current_results: list[RetrievedDocument] = Field(default_factory=list)
    vetted_results: list[RetrievedDocument] = Field(default_factory=list)
    discarded_results: list[RetrievedDocument] = Field(default_factory=list)
    processed_content_ids: set[str] = Field(
        default_factory=set, description="Content IDs of chunks already processed; prevents duplicate retrieval"
    )

    # Decision - drives workflow routing
    decision: Literal["search", "reflect", "finalize", "answer", "retry_no_filters"] = "search"

    # Final output
    answer: str | None = None
    citations: list[Citation] | None = None

    # Thought process - detailed step-by-step execution log
    # Each entry has {"step": str, "details": dict, "attempt": int (optional)}
    # Example steps: "retrieve", "review", "answer_generation"
    thought_process: list[dict[str, Any]] = Field(default_factory=list)
