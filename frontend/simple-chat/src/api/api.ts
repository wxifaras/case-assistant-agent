/**
 * API Client for Knowledge Assistant Chat
 * 
 * Provides functions to interact with the backend chat API.
 */

import { 
    ChatRequest, 
    ChatResponse, 
    ChatErrorResponse, 
    QueryRequest, 
    QueryResponse 
} from "./models";
import { useLogin } from "../authConfig";

const BACKEND_URI = import.meta.env.VITE_BACKEND_URI ? import.meta.env.VITE_BACKEND_URI : "";

/**
 * Build headers for API requests.
 * Includes Authorization header if login is enabled and token is provided.
 */
function getHeaders(idToken: string | undefined): Record<string, string> {
    const headers: Record<string, string> = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    };

    // If using login, add the id token as Bearer authorization
    if (useLogin && idToken) {
        headers["Authorization"] = `Bearer ${idToken}`;
    }

    return headers;
}

/**
 * Send a chat message and get an AI-generated response with citations.
 * 
 * @param request - The chat request containing message and optional filters
 * @param idToken - Optional authentication token
 * @returns ChatResponse with message, citations, and timestamp
 * @throws Error if the request fails
 */
export async function chatApi(request: ChatRequest, idToken: string | undefined): Promise<ChatResponse> {
    const response = await fetch(`${BACKEND_URI}/api/chat`, {
        method: "POST",
        headers: getHeaders(idToken),
        body: JSON.stringify(request)
    });

    const parsedResponse = await response.json();

    if (response.status > 299 || !response.ok) {
        // Handle FastAPI error response format
        const errorMessage = (parsedResponse as ChatErrorResponse).detail 
            || parsedResponse.error 
            || "Unknown error";
        throw new Error(errorMessage);
    }

    return parsedResponse as ChatResponse;
}

/**
 * Send an agentic RAG query and get a response with citations and workflow metadata.
 * Uses the /api/chat/query endpoint with multi-step reasoning.
 * 
 * @param request - The query request containing query and optional session context
 * @param idToken - Optional authentication token
 * @returns QueryResponse with answer, citations, thought_process, and more
 * @throws Error if the request fails
 */
export async function queryApi(request: QueryRequest, idToken: string | undefined): Promise<QueryResponse> {
    const response = await fetch(`${BACKEND_URI}/api/chat/query`, {
        method: "POST",
        headers: getHeaders(idToken),
        body: JSON.stringify(request)
    });

    const parsedResponse = await response.json();

    if (response.status > 299 || !response.ok) {
        // Handle FastAPI error response format
        const errorMessage = (parsedResponse as ChatErrorResponse).detail 
            || parsedResponse.error 
            || "Unknown error";
        throw new Error(errorMessage);
    }

    return parsedResponse as QueryResponse;
}

/**
 * Get the URL for a citation file/document.
 * 
 * @param citationPath - The content_path from a Citation object
 * @returns Full URL to access the citation content
 */
export function getCitationFilePath(citationPath: string): string {
    // Remove leading slash if present for URL construction
    const cleanPath = citationPath.startsWith("/") ? citationPath.slice(1) : citationPath;
    return `${BACKEND_URI}/api/content/${cleanPath}`;
}

