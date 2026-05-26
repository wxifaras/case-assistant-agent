/**
 * API Models for Knowledge Assistant Chat
 * 
 * These interfaces match the backend FastAPI schemas defined in:
 * backend/app/api/schemas/chat.py
 */

// ============================================================================
// Enums
// ============================================================================

/**
 * Role of a message in a conversation.
 */
export const enum MessageRole {
    User = "user",
    Assistant = "assistant",
    System = "system"
}

/**
 * Chat mode selection - determines which API endpoint to use.
 */
export const enum ChatMode {
    /** Standard chat - simple Q&A with /api/chat endpoint */
    Standard = "standard",
    /** Agentic RAG - multi-step reasoning with /api/chat/query endpoint */
    Agentic = "agentic"
}

// ============================================================================
// Request Types
// ============================================================================

/**
 * Individual message in a conversation history.
 */
export type ChatMessage = {
    role: MessageRole;
    content: string;
};

/**
 * Request payload for the /api/chat endpoint.
 */
export type ChatRequest = {
    /** User identifier for tracking and conversation history (dev/test: manual entry) */
    user_id?: string;
    /** The user's question or message */
    message: string;
    /** Previous messages in the conversation for context */
    conversation_history?: ChatMessage[];
    /** Number of search results to retrieve for context (1-20, default: 5) */
    top_k?: number;
};

/**
 * Request payload for the /api/chat/query endpoint (Agentic RAG).
 */
export type QueryRequest = {
    /** The user's question or query */
    query: string;
    /** Session ID (must be valid UUID) for conversation context */
    session_id: string;
    /** Optional user identifier (will be pulled from JWT claim when auth is enabled) */
    user_id?: string;
    /** Previous messages in the conversation */
    conversation_history?: ChatMessage[];
    /** Enable streaming response (not yet implemented) */
    stream?: boolean;
    /** When the previous turn returned a pending_escalation, the user's chosen action. */
    escalation_choice?: EscalationChoice;
};

/**
 * Action selected by the user in response to a HITL escalation prompt.
 */
export type EscalationChoice = {
    action: "ticket" | "live_chat" | "cancel";
    note?: string;
};

/**
 * Pending escalation request surfaced when the workflow paused on a HITL
 * request_info event. The frontend should prompt the user to pick one of
 * ``options`` and re-submit the query with ``escalation_choice`` set.
 */
export type PendingEscalation = {
    request_id: string;
    user_query: string;
    reason: string;
    options: string[];
};

// ============================================================================
// Response Types
// ============================================================================

/**
 * Source document citation included in chat response.
 */
export type Citation = {
    /** Unique identifier for the content chunk */
    content_id: string;
    /** Title of the source document */
    document_title?: string;
    /** The relevant text excerpt */
    content: string;
    /** Path to the source document in blob storage */
    content_path?: string;
    /** Page number where the content appears */
    page_number?: number;
    /** Search relevance score (0-1) */
    relevance_score?: number;
};

/**
 * Response payload from the /api/chat endpoint.
 */
export type ChatResponse = {
    /** The AI-generated response */
    message: string;
    /** Source documents used to generate the response */
    citations: Citation[];
    /** When the response was generated (ISO 8601) */
    timestamp: string;
};

/**
 * A step in the agentic workflow thought process.
 */
export type ThoughtStep = {
    /** Step type: retrieve, review, response, etc. */
    step: string;
    /** Details about what happened in this step */
    details: Record<string, unknown>;
};

/**
 * A search attempt in the agentic workflow.
 */
export type SearchAttempt = {
    /** The search query used */
    query: string;
    /** Number of results returned */
    results_count: number;
    /** Attempt number (1-based) */
    attempt: number;
};

/**
 * Response payload from the /api/chat/query endpoint (Agentic RAG).
 */
export type QueryResponse = {
    /** The generated answer text */
    answer: string;
    /** List of citations with document metadata */
    citations: Citation[];
    /** Number of documents retrieved */
    document_count: number;
    /** Session ID for this conversation */
    session_id?: string;
    /** Step-by-step workflow execution log */
    thought_process: ThoughtStep[];
    /** History of search attempts with queries and result counts */
    search_history: SearchAttempt[];
    /** List of decisions made by reflection agent */
    decisions: string[];
    /** Number of search attempts made */
    attempts: number;
    /** When the response was generated (ISO 8601) */
    timestamp: string;
    /** Set when the workflow paused on a HITL escalation request. */
    pending_escalation?: PendingEscalation | null;
    /** Escalation action that was executed (if any). */
    escalation_action?: "ticket" | "live_chat" | "cancel" | null;
    /** Result returned by the executed escalation tool (ticket/chat metadata). */
    escalation_result?: Record<string, unknown> | null;
};

/**
 * Error response from API.
 */
export type ChatErrorResponse = {
    detail: string;
};

/**
 * Union type for API response handling.
 */
export type ChatResponseOrError = ChatResponse | ChatErrorResponse;

/**
 * Unified response type that normalizes both ChatResponse and QueryResponse.
 * Used internally by the Chat component to handle both endpoint responses.
 */
export type UnifiedChatResponse = {
    /** The AI-generated response (from message or answer field) */
    message: string;
    /** Source documents used to generate the response */
    citations: Citation[];
    /** When the response was generated (ISO 8601) */
    timestamp: string;
    /** The chat mode that generated this response */
    mode: ChatMode;
    /** Agentic-only: Session ID for conversation context */
    session_id?: string;
    /** Agentic-only: Number of documents retrieved */
    document_count?: number;
    /** Agentic-only: Step-by-step workflow execution log */
    thought_process?: ThoughtStep[];
    /** Agentic-only: History of search attempts */
    search_history?: SearchAttempt[];
    /** Agentic-only: Reflection agent decisions */
    decisions?: string[];
    /** Agentic-only: Number of search attempts made */
    attempts?: number;
    /** Agentic-only: HITL pause — render escalation prompt when present. */
    pending_escalation?: PendingEscalation | null;
    /** Agentic-only: Escalation action that was executed. */
    escalation_action?: "ticket" | "live_chat" | "cancel" | null;
    /** Agentic-only: Result returned by the executed escalation tool. */
    escalation_result?: Record<string, unknown> | null;
};

/**
 * Convert a ChatResponse to UnifiedChatResponse.
 */
export function toUnifiedResponse(response: ChatResponse): UnifiedChatResponse {
    return {
        message: response.message,
        citations: response.citations,
        timestamp: response.timestamp,
        mode: ChatMode.Standard
    };
}

/**
 * Convert a QueryResponse to UnifiedChatResponse.
 */
export function queryToUnifiedResponse(response: QueryResponse): UnifiedChatResponse {
    return {
        message: response.answer,
        citations: response.citations,
        timestamp: response.timestamp,
        mode: ChatMode.Agentic,
        session_id: response.session_id,
        document_count: response.document_count,
        thought_process: response.thought_process,
        search_history: response.search_history,
        decisions: response.decisions,
        attempts: response.attempts,
        pending_escalation: response.pending_escalation,
        escalation_action: response.escalation_action,
        escalation_result: response.escalation_result
    };
}

/**
 * Type guard to check if response is an error.
 */
export function isChatError(response: ChatResponseOrError): response is ChatErrorResponse {
    return (response as ChatErrorResponse).detail !== undefined;
}
