import { useRef, useState, useEffect } from "react";
import { Panel, DefaultButton, SpinButton, PanelType } from "@fluentui/react";
import { SparkleFilled } from "@fluentui/react-icons";

import styles from "./Chat.module.css";

import {
    chatApi,
    queryApi,
    ChatRequest,
    QueryRequest,
    ChatMessage,
    MessageRole,
    ChatMode,
    UnifiedChatResponse,
    EscalationChoice,
    toUnifiedResponse,
    queryToUnifiedResponse
} from "../../api";
import { Answer, AnswerError, AnswerLoading } from "../../components/Answer";
import { QuestionInput } from "../../components/QuestionInput";
import { UserChatMessage } from "../../components/UserChatMessage";
import { SettingsButton } from "../../components/SettingsButton";
import { ClearChatButton } from "../../components/ClearChatButton";
import { UserIdInput } from "../../components/UserIdInput";
import { ExampleList } from "../../components/Example";
import { AnalysisPanel, AnalysisPanelTabs } from "../../components/AnalysisPanel";
import { useLogin, getToken } from "../../authConfig";
import { useMsal } from "@azure/msal-react";
import { TokenClaimsDisplay } from "../../components/TokenClaimsDisplay";

/**
 * Represents a conversation turn: user question and assistant response
 */
type ConversationTurn = {
    userMessage: string;
    response: UnifiedChatResponse;
};

const Chat = () => {
    // Settings state
    const [isConfigPanelOpen, setIsConfigPanelOpen] = useState(false);
    const [retrieveCount, setRetrieveCount] = useState<number>(5);

    // Chat mode is locked to Agentic RAG; the Standard chat UI was removed.
    const chatMode = ChatMode.Agentic;

    // Session ID for agentic mode (tracks conversation context)
    const [sessionId, setSessionId] = useState<string>(() => {
        // Generate UUID v4 for new sessions
        return crypto.randomUUID();
    });

    // User ID state (dev/test - persisted to sessionStorage for unique ID per tab)
    // Auto-generate unique user ID per tab (until auth is enabled)
    const [userId, setUserId] = useState<string>(() => {
        const existing = sessionStorage.getItem("test_user_id");
        if (existing) {
            return existing;
        }
        // Generate new user ID with test prefix and persist it
        const randomSuffix = crypto.randomUUID().split('-')[0]; // Use first segment of UUID
        const newUserId = `test-user-${randomSuffix}`;
        sessionStorage.setItem("test_user_id", newUserId);
        return newUserId;
    });

    // Persist userId to sessionStorage when it changes
    useEffect(() => {
        if (userId) {
            sessionStorage.setItem("test_user_id", userId);
        }
    }, [userId]);

    // Chat state
    const lastQuestionRef = useRef<string>("");
    const chatMessageStreamEnd = useRef<HTMLDivElement | null>(null);
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const [error, setError] = useState<unknown>();
    const [answers, setAnswers] = useState<ConversationTurn[]>([]);
    // When the user picks an escalation option we display that action (instead of
    // re-showing the original question) along with a status message while the
    // workflow resumes. Cleared when the request finishes.
    const [activeEscalation, setActiveEscalation] = useState<EscalationChoice | undefined>(undefined);

    // Citation panel state
    const [activeCitation, setActiveCitation] = useState<string>();
    const [selectedAnswer, setSelectedAnswer] = useState<number>(0);
    const [activeAnalysisPanelTab, setActiveAnalysisPanelTab] = useState<AnalysisPanelTabs>(AnalysisPanelTabs.CitationTab);

    // Auth
    const client = useLogin ? useMsal().instance : undefined;

    /**
     * Build conversation history from previous turns for context
     */
    const buildConversationHistory = (): ChatMessage[] => {
        return answers.flatMap(turn => [
            { role: MessageRole.User, content: turn.userMessage },
            { role: MessageRole.Assistant, content: turn.response.message }
        ]);
    };

    /**
     * Send a message to the appropriate chat API based on selected mode
     */
    const makeApiRequest = async (question: string, escalationChoice?: EscalationChoice) => {
        lastQuestionRef.current = question;

        error && setError(undefined);
        setIsLoading(true);
        setActiveCitation(undefined);

        const token = client ? await getToken(client) : undefined;
        const trimmedUserId = userId?.trim() ?? "";

        try {
            let unifiedResponse: UnifiedChatResponse;

            if (chatMode === ChatMode.Agentic) {
                // Use agentic RAG endpoint
                const request: QueryRequest = {
                    query: question,
                    session_id: sessionId,
                    user_id: trimmedUserId !== "" ? trimmedUserId : undefined,
                    escalation_choice: escalationChoice
                    // conversation_history: buildConversationHistory() // Commented out - backend loads from Cosmos DB
                };

                const response = await queryApi(request, token?.accessToken);
                unifiedResponse = queryToUnifiedResponse(response);
                
                // Update session ID if backend returns a different one
                if (response.session_id && response.session_id !== sessionId) {
                    setSessionId(response.session_id);
                }
            } else {
                // Use standard chat endpoint
                const request: ChatRequest = {
                    user_id: trimmedUserId !== "" ? trimmedUserId : undefined,
                    message: question,
                    conversation_history: buildConversationHistory(),
                    top_k: retrieveCount
                };

                const response = await chatApi(request, token?.accessToken);
                unifiedResponse = toUnifiedResponse(response);
            }
            
            setAnswers(prev => {
                // For a resumed HITL turn, keep the previous turn (with its
                // escalation buttons) intact and append the result as a new
                // assistant message labeled with the action the user picked.
                if (escalationChoice) {
                    return [
                        ...prev,
                        {
                            userMessage: escalationActionLabel(escalationChoice),
                            response: unifiedResponse
                        }
                    ];
                }
                return [...prev, { userMessage: question, response: unifiedResponse }];
            });
        } catch (e) {
            setError(e);
        } finally {
            setIsLoading(false);
            setActiveEscalation(undefined);
        }
    };

    /**
     * Resume a paused HITL workflow with the user's chosen escalation action.
     */
    const onEscalationChoice = (choice: EscalationChoice) => {
        if (isLoading) return;
        setActiveEscalation(choice);
        makeApiRequest(lastQuestionRef.current, choice);
    };

    /**
     * Clear the chat and reset state
     */
    const clearChat = () => {
        lastQuestionRef.current = "";
        error && setError(undefined);
        setActiveCitation(undefined);
        setAnswers([]);
        setIsLoading(false);
        setActiveEscalation(undefined);
        setSessionId(crypto.randomUUID()); // Generate new session ID for agentic mode
    };

    // Auto-scroll to bottom when loading or new answers
    useEffect(() => chatMessageStreamEnd.current?.scrollIntoView({ behavior: "smooth" }), [isLoading]);
    useEffect(() => chatMessageStreamEnd.current?.scrollIntoView({ behavior: "auto" }), [answers]);

    // Event handlers
    const onRetrieveCountChange = (_ev?: React.SyntheticEvent<HTMLElement, Event>, newValue?: string) => {
        setRetrieveCount(parseInt(newValue || "5"));
    };

    const onShowCitation = (citation: string, index: number) => {
        setActiveCitation(citation);
        setSelectedAnswer(index);
    };

    /**
     * Human-readable label for an escalation choice (used while the workflow
     * resumes so the user sees the action they picked instead of their original
     * question being re-displayed).
     */
    const escalationActionLabel = (choice: EscalationChoice): string => {
        switch (choice.action) {
            case "ticket":
                return "Create helpdesk ticket";
            case "live_chat":
                return "Connect to tier-2 live chat";
            case "cancel":
                return "Ask a new question";
            default:
                return "Escalation action";
        }
    };

    /**
     * Status message shown while the escalation action is being performed.
     */
    const escalationLoadingMessage = (choice: EscalationChoice): string => {
        switch (choice.action) {
            case "ticket":
                return "Creating helpdesk ticket";
            case "live_chat":
                return "Connecting you to a tier-2 agent";
            case "cancel":
                return "Ending this request";
            default:
                return "Processing";
        }
    };

    return (
        <div className={styles.container}>
            <div className={styles.commandsContainer}>
                <UserIdInput 
                    userId={userId} 
                    onUserIdChange={setUserId} 
                    disabled={isLoading}
                />
                <ClearChatButton className={styles.commandButton} onClick={clearChat} disabled={!lastQuestionRef.current || isLoading} />
                <SettingsButton className={styles.commandButton} onClick={() => setIsConfigPanelOpen(!isConfigPanelOpen)} />
            </div>
            <div className={styles.chatRoot}>
                <div className={styles.chatContainer}>
                    {!lastQuestionRef.current ? (
                        <div className={styles.chatEmptyState}>
                            <SparkleFilled fontSize={"72px"} primaryFill={"rgba(115, 118, 225, 1)"} aria-hidden="true" aria-label="Chat logo" />
                            <h1 className={styles.chatEmptyStateTitle}>Chat with your personal assistant</h1>
                            <h2 className={styles.chatEmptyStateSubtitle}>Ask your question against our document data store or try an example</h2>
                            <ExampleList
                                onExampleClicked={question => makeApiRequest(question)}
                                disabled={isLoading}
                            />
                        </div>
                    ) : (
                        <div className={styles.chatMessageStream}>
                            {answers.map((turn, index) => {
                                const isLastTurn = index === answers.length - 1;
                                const hasPendingEscalation = !!turn.response.pending_escalation;
                                // Buttons remain visible on every pending-escalation turn
                                // (so the user can see what they chose), but only the most
                                // recent pending turn is interactive.
                                const escalationHandler = hasPendingEscalation && isLastTurn
                                    ? onEscalationChoice
                                    : undefined;
                                return (
                                    <div key={index}>
                                        <UserChatMessage message={turn.userMessage} />
                                        <div className={styles.chatMessageGpt}>
                                            <Answer
                                                answer={turn.response}
                                                isSelected={selectedAnswer === index && activeCitation !== undefined}
                                                onCitationClicked={c => onShowCitation(c, index)}
                                                onEscalationChoice={escalationHandler}
                                                escalationDisabled={isLoading || !isLastTurn}
                                            />
                                        </div>
                                    </div>
                                );
                            })}
                            {isLoading && (
                                <>
                                    <UserChatMessage
                                        message={
                                            activeEscalation
                                                ? escalationActionLabel(activeEscalation)
                                                : lastQuestionRef.current
                                        }
                                    />
                                    <div className={styles.chatMessageGptMinWidth}>
                                        <AnswerLoading
                                            message={
                                                activeEscalation
                                                    ? escalationLoadingMessage(activeEscalation)
                                                    : undefined
                                            }
                                        />
                                    </div>
                                </>
                            )}
                            {error ? (
                                <>
                                    <UserChatMessage message={lastQuestionRef.current} />
                                    <div className={styles.chatMessageGptMinWidth}>
                                        <AnswerError error={error.toString()} onRetry={() => makeApiRequest(lastQuestionRef.current)} />
                                    </div>
                                </>
                            ) : null}
                            <div ref={chatMessageStreamEnd} />
                        </div>
                    )}

                    <div className={styles.chatInput}>
                        <QuestionInput
                            clearOnSend
                            placeholder="Type a new question"
                            disabled={isLoading}
                            onSend={question => makeApiRequest(question)}
                        />
                    </div>
                </div>

                <Panel
                    headerText="Configure answer generation"
                    isOpen={isConfigPanelOpen}
                    isBlocking={false}
                    onDismiss={() => setIsConfigPanelOpen(false)}
                    closeButtonAriaLabel="Close"
                    onRenderFooterContent={() => <DefaultButton onClick={() => setIsConfigPanelOpen(false)}>Close</DefaultButton>}
                    isFooterAtBottom={true}
                >
                    <SpinButton
                        className={styles.chatSettingsSeparator}
                        label="Retrieve this many search results:"
                        min={1}
                        max={20}
                        defaultValue={retrieveCount.toString()}
                        onChange={onRetrieveCountChange}
                    />

                    {useLogin && <TokenClaimsDisplay />}
                </Panel>

                {/* Citation Panel - Shows selected citation content */}
                {activeCitation && answers[selectedAnswer] && (
                    <Panel
                        headerText="Citation Details"
                        isOpen={activeCitation !== undefined}
                        isBlocking={false}
                        onDismiss={() => setActiveCitation(undefined)}
                        closeButtonAriaLabel="Close"
                        onRenderFooterContent={() => <DefaultButton onClick={() => setActiveCitation(undefined)}>Close</DefaultButton>}
                        isFooterAtBottom={true}
                        type={PanelType.large}
                    >
                        <AnalysisPanel
                            className={styles.chatAnalysisPanel}
                            activeCitation={activeCitation}
                            onActiveTabChanged={setActiveAnalysisPanelTab}
                            activeTab={activeAnalysisPanelTab}
                            citationHeight="600px"
                            answer={answers[selectedAnswer].response}
                        />
                    </Panel>
                )}
            </div>
        </div>
    );
};

export default Chat;
