/**
 * HTTP client for the Case Assistant FastAPI backend (`/api/chat/query`).
 *
 * Maps Teams activity fields to the backend's `QueryRequest` schema and
 * returns the parsed `QueryResponse`. The session_id is derived from the
 * Teams conversation id (namespaced UUID v5 so it is stable per conversation
 * and passes the backend's UUID validation).
 */

import { createHash } from "crypto";

export interface Citation {
  content_id?: string;
  document_id?: string;
  document_title?: string;
  content?: string;
  content_path?: string;
  page_number?: number;
  relevance_score?: number;
  [key: string]: unknown;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  document_count: number;
  session_id?: string | null;
  thought_process?: Array<Record<string, unknown>>;
  search_history?: Array<Record<string, unknown>>;
  decisions?: string[];
  attempts?: number;
  timestamp?: string;
}

export interface QueryInput {
  query: string;
  userId: string;
  sessionId: string;
  bearerToken?: string;
}

/**
 * Produce a deterministic UUID v5-style identifier from an arbitrary string.
 *
 * The backend validates session_id as a UUID, but the Teams conversation id is
 * an opaque string. Hashing it yields a stable 36-char UUID for the lifetime
 * of the conversation without requiring a separate mapping store.
 */
export function conversationIdToSessionId(conversationId: string): string {
  const hash = createHash("sha1").update(conversationId).digest("hex");
  return [
    hash.substring(0, 8),
    hash.substring(8, 12),
    // Force version 5 nibble
    "5" + hash.substring(13, 16),
    // Force variant 10xx nibble
    ((parseInt(hash.substring(16, 17), 16) & 0x3) | 0x8).toString(16) +
      hash.substring(17, 20),
    hash.substring(20, 32),
  ].join("-");
}

export class AgentClient {
  constructor(private readonly baseUrl: string) {
    if (!baseUrl) {
      throw new Error("AGENT_URL must be configured.");
    }
  }

  async query(input: QueryInput): Promise<QueryResponse> {
    const url = `${this.baseUrl.replace(/\/$/, "")}/api/chat/query`;

    const body: Record<string, unknown> = {
      query: input.query,
      user_id: input.userId,
      session_id: input.sessionId,
    };

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (input.bearerToken) {
      headers["Authorization"] = `Bearer ${input.bearerToken}`;
    }

    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(
        `Agent query failed: ${res.status} ${res.statusText}${text ? ` — ${text}` : ""}`,
      );
    }

    return (await res.json()) as QueryResponse;
  }
}
