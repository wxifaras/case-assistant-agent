"""Chat endpoints for Case Assistant Agentic RAG.

This module provides RESTful API endpoints for conversational AI interactions
with agentic retrieval-augmented generation capabilities with workflow orchestration.

Purpose:
    - Agentic RAG workflow with multi-agent orchestration (planning, retrieval, reflection, generation)
    - Conversation history management with Cosmos DB persistence
    - Bearer token authentication for production security
    - Comprehensive citation tracking and source attribution

Architecture:
    - Uses dependency injection container for service management
    - ChatService orchestrates both simple RAG and agentic workflow
    - MAF (Microsoft Agent Framework) workflow for multi-agent coordination
    - Cosmos DB for conversation persistence and history

Endpoints:
    POST   /api/chat/query                                 - Agentic RAG with workflow orchestration
    GET    /api/chat/history/{session_id}                  - Get conversation message history
    GET    /api/chat/conversations/{user_id}               - List user's conversation sessions
    GET    /api/chat/conversations/{user_id}/{session_id}  - Retrieve conversation thread
    DELETE /api/chat/conversations/{user_id}/{session_id}  - Delete specific conversation
    DELETE /api/chat/conversations/{user_id}               - Clear all user conversations

Example Usage:
    ```bash
    # Agentic RAG query with conversation context
    curl -X POST http://localhost:8000/api/chat/query \\
      -H "Content-Type: application/json" \\
      -d '{
        "query": "What are Azure Cosmos DB best practices?",
        "user_id": "user123",
        "session_id": "session456"
      }'

    # Get conversation message history
    curl -X GET "http://localhost:8000/api/chat/history/session456?user_id=user123"

    # Get last 20 messages only
    curl -X GET "http://localhost:8000/api/chat/history/session456?user_id=user123&max_messages=20"

    # List user conversations
    curl -X GET http://localhost:8000/api/chat/conversations/user123

    # Get conversation thread
    curl -X GET http://localhost:8000/api/chat/conversations/user123/session456
    ```

Response Formats:
    - Agentic query: Returns answer with citations, document count, and workflow metadata
    - Conversations: Returns session metadata with message counts and timestamps

Security:
    - Optional Bearer token authentication (configured via HTTPBearer)
    - Production deployments should set auto_error=True for strict auth
    - Tokens validated against Azure AD/Entra ID in production

Dependencies:
    - IChatService: Orchestrates RAG and workflow execution
    - IChatHistoryService: Manages conversation history and persistence
    - Container: Dependency injection for service management
    - Logger: Centralized logging with Application Insights integration
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import get_chat_service, get_logger
from app.api.schemas.chat import QueryRequest, QueryResponse
from app.services.chat_service import IChatService

# Initialize router - redirect_slashes=False prevents 307 redirects that lose auth headers
router = APIRouter(prefix="/chat", tags=["Chat"], redirect_slashes=False)

# Security scheme for Bearer token authentication (auto_error=False for optional auth in dev)
security = HTTPBearer(auto_error=False)


def _require_user_id(user_id: str | None) -> str:
    if user_id is None or not user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user id is required",
        )
    return user_id


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    logger=Depends(get_logger),
) -> str | None:
    """
    Verify the Bearer token from the Authorization header.

    This is a placeholder implementation. In production, you should:
    - Validate JWT tokens against Azure AD / Entra ID
    - Check token expiration, audience, issuer, etc.
    - Extract user claims for authorization

    Note: Currently allows unauthenticated requests for development.
    Set auto_error=True on HTTPBearer for production.

    Args:
        credentials: The HTTP Bearer credentials from the request (optional).

    Returns:
        The token string if provided, None otherwise.
    """
    if not credentials or not credentials.credentials:
        # Allow unauthenticated requests for development
        # TODO: In production, raise 401 here
        logger.warning("Unauthenticated request - allowing for development")
        return None

    # TODO: Implement proper JWT validation with Azure AD
    # For now, we just check that a token is present
    token = credentials.credentials

    # Placeholder: In production, validate the token here
    # Example with Azure AD:
    # - Decode and verify JWT signature
    # - Check iss (issuer), aud (audience), exp (expiration)
    # - Extract user identity from claims

    logger.debug("Token authentication successful")
    return token


@router.post("/query", response_model=QueryResponse)
async def agentic_query(
    request: QueryRequest,
    chat_service: IChatService = Depends(get_chat_service),
    logger=Depends(get_logger),
) -> QueryResponse:
    """
    Execute agentic RAG query using MAF workflow orchestration.

    **Workflow Steps:**
    1. Query analysis and planning
    2. Iterative search with query rewriting
    3. Reflection on search results
    4. Answer generation from aggregated results

    **Parameters:**
    - **query**: User's question (1-2000 characters)
    - **session_id**: Session ID (must be valid UUID) for conversation context
    - **user_id**: Optional user identifier (will be pulled from JWT claim when auth is enabled)
    - **chat_history**: Optional list of previous messages
    - **filters**: Optional search filters to narrow results
    - **stream**: Enable streaming response (not yet implemented)

    **Returns:**
    - **answer**: Generated answer with inline citations
    - **citations**: List of document IDs cited in answer
    - **document_count**: Number of documents retrieved
    - **session_id**: Session ID for this conversation
    """
    try:
        query_preview = request.query[:100] + ("..." if len(request.query) > 100 else "")
        logger.info(f"Received agentic query: {query_preview}")

        # Execute agentic RAG workflow
        response = await chat_service.query_async(
            query=request.query,
            session_id=request.session_id,
            user_id=request.user_id,
            chat_history=request.chat_history,
            filters=request.filters,
        )

        logger.info(f"Agentic query completed: {len(response.answer)} chars, {len(response.citations)} citations")
        return response

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Agentic query failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Query processing failed: {str(e)}"
        ) from e


@router.get("/history/{session_id}")
async def get_conversation_history(
    session_id: str,
    user_id: str,
    chat_service: IChatService = Depends(get_chat_service),
    logger=Depends(get_logger),
    max_messages: int | None = None,
) -> dict[str, Any]:
    """
    Get all conversation message history for a specific session.

    **Parameters:**
    - **session_id**: Session identifier (UUID)
    - **user_id**: User identifier (query parameter)
    - **max_messages**: Optional maximum number of recent messages to return (default: all messages)

    **Returns:**
    - All messages in chronological order with role and content

    **Note:** Requires Cosmos DB to be configured.
    """
    try:
        messages = await chat_service.get_user_chat_history(session_id, user_id, max_messages)

        return {
            "session_id": session_id,
            "user_id": user_id,
            "message_count": len(messages),
            "messages": messages,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to retrieve conversation history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve conversation history: {str(e)}",
        ) from e


@router.get("/conversations/{user_id}")
async def list_user_conversations(
    user_id: str,
    chat_service: IChatService = Depends(get_chat_service),
    logger=Depends(get_logger),
    max_results: int = 100,
) -> dict[str, Any]:
    """
    List all conversation sessions for a user.

    **Parameters:**
    - **user_id**: User identifier
    - **max_results**: Maximum number of sessions to return (default: 100)

    **Returns:**
    - List of conversation sessions with metadata

    **Note:** Requires Cosmos DB to be configured.
    """
    try:
        sessions = await chat_service.list_user_chat_sessions(user_id, max_results)

        return {"user_id": user_id, "session_count": len(sessions), "sessions": sessions}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to list conversations: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to retrieve conversations: {str(e)}"
        ) from e


@router.get("/conversations/{user_id}/{session_id}")
async def get_conversation(
    user_id: str,
    session_id: str,
    chat_service: IChatService = Depends(get_chat_service),
    logger=Depends(get_logger),
) -> dict[str, Any]:
    """
    Retrieve a specific conversation thread.

    **Parameters:**
    - **user_id**: User identifier
    - **session_id**: Session identifier

    **Returns:**
    - Full conversation thread with all messages

    **Note:** Requires Cosmos DB to be configured.
    """
    try:
        thread = await chat_service.get_user_chat_history(session_id, user_id)

        if not thread:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Conversation not found: {session_id}")

        return {"session_id": session_id, "user_id": user_id, "messages": thread}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to retrieve conversation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to retrieve conversation: {str(e)}"
        ) from e


@router.delete("/conversations/{user_id}/{session_id}")
async def delete_conversation(
    user_id: str,
    session_id: str,
    chat_service: IChatService = Depends(get_chat_service),
    logger=Depends(get_logger),
) -> dict[str, str]:
    """
    Delete a specific conversation thread.

    **Parameters:**
    - **user_id**: User identifier
    - **session_id**: Session identifier

    **Returns:**
    - Confirmation message

    **Note:** Requires Cosmos DB to be configured.
    """
    try:
        await chat_service.delete_user_chat_session(session_id, user_id)

        return {"status": "deleted", "session_id": session_id, "user_id": user_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to delete conversation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete conversation: {str(e)}"
        ) from e


@router.delete("/conversations/{user_id}")
async def clear_user_history(
    user_id: str,
    chat_service: IChatService = Depends(get_chat_service),
    logger=Depends(get_logger),
) -> dict[str, Any]:
    """
    Delete all conversation threads for a user.

    **Parameters:**
    - **user_id**: User identifier

    **Returns:**
    - Number of conversations deleted

    **Note:** Requires Cosmos DB to be configured.
    """
    try:
        count = await chat_service.clear_user_chat_history(user_id)

        return {"status": "cleared", "user_id": user_id, "deleted_count": count}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to clear user history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clear user history: {str(e)}"
        ) from e
