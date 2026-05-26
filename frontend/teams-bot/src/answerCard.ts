/**
 * Adaptive Card builders for agent responses.
 */

import type { Attachment } from "@microsoft/agents-activity";
import { CardFactory } from "@microsoft/agents-hosting";
import type { Citation, QueryResponse } from "./agentClient";

const ADAPTIVE_CARD_VERSION = "1.5";

export function buildAnswerCard(
  response: QueryResponse,
  backendUrl?: string,
): Attachment {
  const body: Array<Record<string, unknown>> = [
    {
      type: "TextBlock",
      text: "Case Assistant",
      weight: "Bolder",
      size: "Medium",
      color: "Accent",
    },
    {
      type: "TextBlock",
      text: response.answer || "(no answer returned)",
      wrap: true,
    },
  ];

  const citations = response.citations ?? [];
  if (citations.length > 0) {
    body.push({
      type: "TextBlock",
      text: "Sources",
      weight: "Bolder",
      spacing: "Medium",
      separator: true,
    });
    citations.forEach((c, i) => {
      body.push({
        type: "TextBlock",
        text: formatCitation(c, i + 1, backendUrl),
        wrap: true,
        spacing: "Small",
      });
    });
  }

  return CardFactory.adaptiveCard({
    $schema: "http://adaptivecards.io/schemas/adaptive-card.json",
    type: "AdaptiveCard",
    version: ADAPTIVE_CARD_VERSION,
    body,
  });
}

function formatCitation(c: Citation, index: number, backendUrl?: string): string {
  const title =
    c.document_title ||
    fileNameFromPath(c.content_path) ||
    c.content_id ||
    c.document_id ||
    "Source";
  const page = c.page_number ? ` (p.${c.page_number})` : "";
  const link = citationLink(c, backendUrl);
  const titleWithPage = `${title}${page}`;
  // Use bracketed prefix so Adaptive Cards' markdown parser doesn't treat
  // "1. ..." as an ordered list (which strips/renumbers the prefix).
  const linkedTitle = link ? `[${titleWithPage}](${link})` : titleWithPage;
  return `**[${index}]** ${linkedTitle}`;
}

/**
 * Mirror the React UI's `getCitationFilePath`: route through the backend's
 * `/api/content/{path}` proxy so authenticated blob retrieval is consistent.
 */
function citationLink(c: Citation, backendUrl?: string): string | undefined {
  if (!c.content_path || !backendUrl) return undefined;
  const cleanPath = c.content_path.startsWith("/")
    ? c.content_path.slice(1)
    : c.content_path;
  return `${backendUrl.replace(/\/$/, "")}/api/content/${cleanPath}`;
}

function fileNameFromPath(path: string | undefined): string | undefined {
  if (!path) return undefined;
  const name = decodeURIComponent(path.split(/[\\/]/).pop() || "");
  return name || undefined;
}
