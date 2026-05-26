import { useMemo } from "react";
import { Stack, PrimaryButton, DefaultButton } from "@fluentui/react";
import DOMPurify from "dompurify";

import styles from "./Answer.module.css";

import { UnifiedChatResponse, Citation, EscalationChoice } from "../../api";
import { AnswerIcon } from "./AnswerIcon";
import { AgenticMetadata } from "../AgenticMetadata";

interface Props {
    answer: UnifiedChatResponse;
    isSelected?: boolean;
    onCitationClicked: (citation: string) => void;
    onEscalationChoice?: (choice: EscalationChoice) => void;
    escalationDisabled?: boolean;
}

export const Answer = ({
    answer,
    isSelected,
    onCitationClicked,
    onEscalationChoice,
    escalationDisabled
}: Props) => {
    // Sanitize the message content
    const sanitizedAnswerHtml = useMemo(() => {
        // Convert newlines to <br> for proper display
        const htmlContent = answer.message.replace(/\n/g, "<br>");
        return DOMPurify.sanitize(htmlContent);
    }, [answer.message]);

    const pending = answer.pending_escalation;

    return (
        <Stack className={`${styles.answerContainer} ${isSelected && styles.selected}`} verticalAlign="space-between">
            <Stack.Item>
                <Stack horizontal horizontalAlign="space-between">
                    <AnswerIcon />
                </Stack>
            </Stack.Item>

            <Stack.Item grow>
                <div className={styles.answerText} dangerouslySetInnerHTML={{ __html: sanitizedAnswerHtml }}></div>
            </Stack.Item>

            {pending && (
                <Stack.Item>
                    <Stack horizontal tokens={{ childrenGap: 8 }} style={{ marginTop: 12 }} wrap>
                        <PrimaryButton
                            text="Create helpdesk ticket"
                            disabled={escalationDisabled || !onEscalationChoice}
                            onClick={onEscalationChoice ? () => onEscalationChoice({ action: "ticket" }) : undefined}
                        />
                        <PrimaryButton
                            text="Connect to tier-2 live chat"
                            disabled={escalationDisabled || !onEscalationChoice}
                            onClick={onEscalationChoice ? () => onEscalationChoice({ action: "live_chat" }) : undefined}
                        />
                        <DefaultButton
                            text="Ask a new question"
                            disabled={escalationDisabled || !onEscalationChoice}
                            onClick={onEscalationChoice ? () => onEscalationChoice({ action: "cancel" }) : undefined}
                        />
                    </Stack>
                </Stack.Item>
            )}

            {answer.citations.length > 0 && (
                <Stack.Item>
                    <Stack horizontal wrap tokens={{ childrenGap: 5 }}>
                        <span className={styles.citationLearnMore}>Citations:</span>
                        {answer.citations.map((citation: Citation, i: number) => {
                            const displayTitle = citation.document_title || citation.content_id;
                            const pageInfo = citation.page_number ? ` (p.${citation.page_number})` : "";
                            return (
                                <a 
                                    key={i} 
                                    className={styles.citation} 
                                    title={`${displayTitle}${pageInfo} - Score: ${citation.relevance_score?.toFixed(2) || 'N/A'}`}
                                    onClick={() => onCitationClicked(citation.content_path || citation.content_id)}
                                >
                                    {`${i + 1}. ${displayTitle}${pageInfo}`}
                                </a>
                            );
                        })}
                    </Stack>
                </Stack.Item>
            )}

            {/* Agentic workflow metadata (only shown for agentic mode responses;
                hidden for completed escalation results so the user just sees the outcome). */}
            {!answer.escalation_action && <AgenticMetadata response={answer} />}
        </Stack>
    );
};
