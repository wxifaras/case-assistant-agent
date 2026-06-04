import { useRef, useState, useEffect } from "react";
import DOMPurify from "dompurify";
import { SparkleFilled } from "@fluentui/react-icons";

import styles from "./Chat.module.css";

import { sendFoundryTurn, isFoundryConfigured } from "../../api";
import { QuestionInput } from "../../components/QuestionInput";
import { UserChatMessage } from "../../components/UserChatMessage";
import { ClearChatButton } from "../../components/ClearChatButton";
import { SharePointSiteSelector } from "../../components/SharePointSiteSelector";

/** A single assistant reply paired with the user message that produced it. */
type ConversationTurn = {
    userMessage: string;
    answer: string;
};

/** Parse the comma-separated SharePoint site list from the environment. */
function parseSites(): string[] {
    return (import.meta.env.VITE_SHAREPOINT_SITES ?? "")
        .split(",")
        .map(site => site.trim())
        .filter(site => site.length > 0);
}

const Chat = () => {
    const sites = parseSites();

    // Selected SharePoint site that scopes the conversation.
    const [selectedSite, setSelectedSite] = useState<string | undefined>(undefined);

    // Conversation state.
    const [turns, setTurns] = useState<ConversationTurn[]>([]);
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const [error, setError] = useState<string | undefined>(undefined);

    // Foundry maintains context server-side; we only track the latest response id.
    const previousResponseId = useRef<string | undefined>(undefined);
    const lastQuestionRef = useRef<string>("");
    const chatMessageStreamEnd = useRef<HTMLDivElement | null>(null);

    const hasConversation = turns.length > 0 || isLoading || !!error;

    /** Send a message to the Foundry agent for the selected site. */
    const makeApiRequest = async (question: string) => {
        if (!selectedSite) {
            setError("Please select a SharePoint site before chatting.");
            return;
        }

        lastQuestionRef.current = question;
        setError(undefined);
        setIsLoading(true);

        try {
            const result = await sendFoundryTurn({
                siteName: selectedSite,
                message: question,
                previousResponseId: previousResponseId.current
            });
            previousResponseId.current = result.responseId ?? previousResponseId.current;
            setTurns(prev => [...prev, { userMessage: question, answer: result.text }]);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setIsLoading(false);
        }
    };

    /** Reset the conversation (and its server-side context). */
    const clearChat = () => {
        lastQuestionRef.current = "";
        previousResponseId.current = undefined;
        setTurns([]);
        setError(undefined);
        setIsLoading(false);
    };

    // Changing the site starts a fresh conversation/context.
    const onSiteChange = (site: string) => {
        setSelectedSite(site);
        clearChat();
    };

    // Auto-scroll to the latest message.
    useEffect(() => chatMessageStreamEnd.current?.scrollIntoView({ behavior: "smooth" }), [isLoading]);
    useEffect(() => chatMessageStreamEnd.current?.scrollIntoView({ behavior: "auto" }), [turns]);

    const inputDisabled = isLoading || !selectedSite;

    return (
        <div className={styles.container}>
            <div className={styles.commandsContainer}>
                <ClearChatButton
                    className={styles.commandButton}
                    onClick={clearChat}
                    disabled={!hasConversation || isLoading}
                />
            </div>

            <div className={styles.siteSelectorContainer}>
                <SharePointSiteSelector
                    sites={sites}
                    selectedSite={selectedSite}
                    onSiteChange={onSiteChange}
                    disabled={isLoading}
                />
            </div>

            <div className={styles.chatRoot}>
                <div className={styles.chatContainer}>
                    {!hasConversation ? (
                        <div className={styles.chatEmptyState}>
                            <SparkleFilled fontSize={"72px"} primaryFill={"rgba(115, 118, 225, 1)"} aria-hidden="true" aria-label="Chat logo" />
                            <h1 className={styles.chatEmptyStateTitle}>Chat with your case assistant</h1>
                            <h2 className={styles.chatEmptyStateSubtitle}>
                                {selectedSite
                                    ? "Ask a question about your case"
                                    : "Pick a SharePoint site above to get started"}
                            </h2>
                            {!isFoundryConfigured() && (
                                <p className={styles.chatConfigWarning}>
                                    The Foundry agent is not configured. Set <code>VITE_FOUNDRY_AGENT</code> (and proxy
                                    secrets) in your <code>.env</code> file.
                                </p>
                            )}
                        </div>
                    ) : (
                        <div className={styles.chatMessageStream}>
                            {turns.map((turn, index) => (
                                <div key={index}>
                                    <UserChatMessage message={turn.userMessage} />
                                    <div className={styles.chatMessageAssistant}>
                                        <div
                                            className={styles.assistantBubble}
                                            dangerouslySetInnerHTML={{
                                                __html: DOMPurify.sanitize(turn.answer.replace(/\n/g, "<br/>"))
                                            }}
                                        />
                                    </div>
                                </div>
                            ))}
                            {isLoading && (
                                <>
                                    <UserChatMessage message={lastQuestionRef.current} />
                                    <div className={styles.chatMessageAssistant}>
                                        <div className={styles.assistantBubble}>Thinking&hellip;</div>
                                    </div>
                                </>
                            )}
                            {error && (
                                <div className={styles.chatMessageAssistant}>
                                    <div className={styles.errorBubble}>{error}</div>
                                </div>
                            )}
                            <div ref={chatMessageStreamEnd} />
                        </div>
                    )}

                    <div className={styles.chatInput}>
                        <QuestionInput
                            clearOnSend
                            placeholder={selectedSite ? "Type your message" : "Select a SharePoint site first"}
                            disabled={inputDisabled}
                            onSend={question => makeApiRequest(question)}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
};

export default Chat;
