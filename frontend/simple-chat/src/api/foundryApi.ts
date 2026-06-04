/**
 * Foundry Responses API client.
 *
 * Sends a user message (scoped to a selected SharePoint site) to a Foundry
 * agent via the OpenAI-compatible Responses API. Conversation context is
 * maintained server-side by Foundry using `previous_response_id`, so the
 * frontend only needs to track the latest response id.
 *
 * Requests are sent to the relative `/foundry/responses` path, which is proxied
 * to the Foundry endpoint (with the `api-key` header injected) by the Vite dev
 * server (see vite.config.ts) or nginx in production (see nginx.conf.template).
 * This keeps the API key out of the browser bundle.
 */

/** Agent name / id (or model deployment) to prompt. */
const FOUNDRY_AGENT = import.meta.env.VITE_FOUNDRY_AGENT ?? "";

/** Optional api-version query string for the Responses API. */
const FOUNDRY_API_VERSION = import.meta.env.VITE_FOUNDRY_API_VERSION ?? "";

/** Whether the Foundry agent is configured. */
export const isFoundryConfigured = (): boolean => FOUNDRY_AGENT.trim() !== "";

/** Parameters for a single Foundry agent turn. */
export type FoundryTurnRequest = {
    /** The selected SharePoint site display name. */
    siteName: string;
    /** The user's chat message. */
    message: string;
    /** Response id from the previous turn (maintains conversation context). */
    previousResponseId?: string;
};

/** Normalized result of a Foundry agent turn. */
export type FoundryTurnResult = {
    /** The assistant's reply text. */
    text: string;
    /** The response id to pass into the next turn for context continuity. */
    responseId?: string;
};

/**
 * Compose the prompt sent to the agent. The selected SharePoint site is
 * provided as explicit context alongside the user's message.
 */
function buildInput(siteName: string, message: string): string {
    return `SharePoint site: ${siteName}\n\nUser question: ${message}`;
}

/**
 * Extract the assistant text from a Responses API payload. Supports the
 * `output_text` convenience field as well as the structured `output` array.
 */
function extractText(payload: any): string {
    if (typeof payload?.output_text === "string" && payload.output_text.length > 0) {
        return payload.output_text;
    }

    const output = payload?.output;
    if (Array.isArray(output)) {
        const parts: string[] = [];
        for (const item of output) {
            const content = item?.content;
            if (Array.isArray(content)) {
                for (const part of content) {
                    if (typeof part?.text === "string") {
                        parts.push(part.text);
                    } else if (typeof part?.text?.value === "string") {
                        parts.push(part.text.value);
                    }
                }
            } else if (typeof content === "string") {
                parts.push(content);
            }
        }
        if (parts.length > 0) {
            return parts.join("\n");
        }
    }

    return "";
}

/**
 * Send one conversational turn to the Foundry agent and return its reply.
 *
 * @throws Error when the agent is not configured or the request fails.
 */
export async function sendFoundryTurn(request: FoundryTurnRequest): Promise<FoundryTurnResult> {
    if (!isFoundryConfigured()) {
        throw new Error("Foundry agent is not configured. Set VITE_FOUNDRY_AGENT in your .env file.");
    }

    const url = FOUNDRY_API_VERSION ? `/foundry/responses?api-version=${encodeURIComponent(FOUNDRY_API_VERSION)}` : "/foundry/responses";

    const body: Record<string, unknown> = {
        model: FOUNDRY_AGENT,
        input: buildInput(request.siteName, request.message)
    };

    if (request.previousResponseId) {
        body.previous_response_id = request.previousResponseId;
    }

    const response = await fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            Accept: "application/json"
        },
        body: JSON.stringify(body)
    });

    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
        const message = payload?.error?.message || payload?.message || payload?.detail || `Foundry request failed (${response.status})`;
        throw new Error(message);
    }

    const text = extractText(payload).trim();
    return {
        text: text || "(The agent returned an empty response.)",
        responseId: typeof payload?.id === "string" ? payload.id : undefined
    };
}
