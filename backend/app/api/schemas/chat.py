"""API schemas for agentic RAG chat interactions.

This module defines all Pydantic models used as API contracts for the
agentic RAG query endpoint, covering request/response shapes, search
filters, and conversation history.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.chat import Citation


class MessageRole(StrEnum):
    """Role of a message in a conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatHistoryMessage(BaseModel):
    """Message in conversation history for the agentic RAG workflow."""

    role: MessageRole = Field(..., description="Role of the message sender")
    content: str = Field(..., min_length=1, description="Message content")


class SearchFilters(BaseModel):
    """Optional search filters for narrowing results."""

    date_from: str | None = Field(
        default=None,
        description="Filter documents published on or after this date (ISO 8601, e.g. '2024-01-01')",
    )
    date_to: str | None = Field(
        default=None,
        description="Filter documents published on or before this date (ISO 8601, e.g. '2024-12-31')",
    )
    document_type: str | None = Field(
        default=None,
        description="Filter by document type (e.g. 'pdf', 'docx')",
    )
    category: str | None = Field(
        default=None,
        description="Filter by document category",
    )
    custom: str | None = Field(
        default=None,
        description="Raw OData filter expression appended to the generated filter",
    )


class QueryRequest(BaseModel):
    """Request payload for the agentic RAG query endpoint."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The user's question or query",
    )
    session_id: str = Field(
        ...,
        description="Session ID (must be valid UUID) for conversation context",
    )
    user_id: str | None = Field(
        default=None,
        description="Optional user identifier (will be pulled from JWT claim when auth is enabled)",
    )
    chat_history: list[ChatHistoryMessage] | None = Field(
        default=None,
        description="Previous messages in the conversation",
    )
    filters: SearchFilters | None = Field(
        default=None,
        description="Optional filters to narrow search results",
    )
    stream: bool = Field(
        default=False,
        description="Enable streaming response (not yet implemented)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": "What are the best practices for Azure Cosmos DB partitioning?",
                    "user_id": "user123",
                    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "filters": {"document_type": "pdf"},
                    "stream": False,
                }
            ]
        }
    }


class QueryResponse(BaseModel):
    """Response payload for agentic RAG query endpoint.

    Attributes:
        answer: The generated answer text.
        citations: List of citations with document metadata.
        document_count: Number of documents retrieved.
        session_id: Session ID for this conversation.
        thought_process: Step-by-step workflow execution log with details.
        search_history: History of search attempts with queries and results.
        decisions: List of decisions made by reflection agent per iteration.
        attempts: Number of search attempts made.
        timestamp: Response generation timestamp.
    """

    answer: str = Field(..., description="The generated answer text")
    citations: list[Citation] = Field(
        default_factory=list, description="List of citations with document metadata (title, path, relevance score)"
    )
    document_count: int = Field(default=0, description="Number of documents retrieved")
    session_id: str | None = Field(default=None, description="Session ID for this conversation")
    thought_process: list[dict[str, Any]] = Field(
        default_factory=list, description="Step-by-step workflow execution log (retrieve, review, response steps)"
    )
    search_history: list[dict[str, Any]] = Field(
        default_factory=list, description="History of search attempts with queries and result counts"
    )
    decisions: list[str] = Field(
        default_factory=list, description="List of decisions made by reflection agent (retry or finalize)"
    )
    attempts: int = Field(default=0, description="Number of search attempts made")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response generation timestamp")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "answer": "Azure Cosmos DB partitioning best practices include...",
                    "citations": ["doc1", "doc2", "doc3"],
                    "document_count": 5,
                    "session_id": "session456",
                    "thought_process": [
                        {"step": "retrieve", "details": {"results_summary": []}},
                        {"step": "review", "details": {"valid_count": 3}},
                        {"step": "response", "details": {"final_answer": "..."}},
                    ],
                    "search_history": [{"query": "...", "results_count": 10, "attempt": 1}],
                    "decisions": ["finalize"],
                    "attempts": 1,
                }
            ]
        }
    }
