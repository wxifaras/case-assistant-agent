import { useState } from "react";
import { 
    Stack, 
    Text, 
    Icon, 
    TooltipHost
} from "@fluentui/react";
import { UnifiedChatResponse, ChatMode, ThoughtStep, SearchAttempt } from "../../api";
import styles from "./AgenticMetadata.module.css";

interface AgenticMetadataProps {
    /** The unified response containing agentic metadata */
    response: UnifiedChatResponse;
}

/**
 * Displays the agentic workflow metadata including thought process,
 * search history, and reflection decisions.
 * Only renders content when the response is from the Agentic RAG mode.
 */
export const AgenticMetadata = ({ response }: AgenticMetadataProps): JSX.Element | null => {
    const [isExpanded, setIsExpanded] = useState(false);
    const [showJsonView, setShowJsonView] = useState(false);

    // Only show for agentic mode responses
    if (response.mode !== ChatMode.Agentic) {
        return null;
    }

    const hasMetadata = 
        (response.thought_process && response.thought_process.length > 0) ||
        (response.search_history && response.search_history.length > 0) ||
        (response.decisions && response.decisions.length > 0);

    if (!hasMetadata) {
        return null;
    }

    const toggleExpand = () => setIsExpanded(!isExpanded);

    return (
        <div className={styles.container}>
            <button 
                className={styles.headerButton} 
                onClick={toggleExpand}
                aria-expanded={isExpanded}
            >
                <Stack horizontal verticalAlign="center" tokens={{ childrenGap: 8 }}>
                    <Icon 
                        iconName="ChatBot" 
                        className={styles.headerIcon}
                    />
                    <Text className={styles.headerText}>
                        Agentic Workflow Details
                    </Text>
                    <Stack horizontal tokens={{ childrenGap: 12 }} className={styles.badges}>
                        {response.attempts !== undefined && response.attempts > 0 && (
                            <TooltipHost content="Number of search attempts">
                                <span className={styles.badge}>
                                    <Icon iconName="Search" className={styles.badgeIcon} />
                                    {response.attempts} {response.attempts === 1 ? "attempt" : "attempts"}
                                </span>
                            </TooltipHost>
                        )}
                        {response.document_count !== undefined && response.document_count > 0 && (
                            <TooltipHost content="Documents retrieved">
                                <span className={styles.badge}>
                                    <Icon iconName="DocumentSet" className={styles.badgeIcon} />
                                    {response.document_count} docs
                                </span>
                            </TooltipHost>
                        )}
                    </Stack>
                    <Icon 
                        iconName={isExpanded ? "ChevronUp" : "ChevronDown"} 
                        className={styles.chevron}
                    />
                </Stack>
            </button>

            {isExpanded && (
                <div className={styles.content}>
                    {/* Thought Process Section */}
                    {response.thought_process && response.thought_process.length > 0 && (
                        <div className={styles.section}>
                            <Stack horizontal horizontalAlign="space-between" verticalAlign="center" style={{ marginBottom: '12px' }}>
                                <button 
                                    onClick={() => setShowJsonView(!showJsonView)}
                                    style={{
                                        padding: '6px 12px',
                                        fontSize: '12px',
                                        backgroundColor: showJsonView ? '#0078d4' : '#f3f2f1',
                                        color: showJsonView ? '#fff' : '#000',
                                        border: '1px solid #ccc',
                                        borderRadius: '4px',
                                        cursor: 'pointer',
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: '4px'
                                    }}
                                >
                                    <Icon iconName="Code" />
                                    {showJsonView ? 'Show Formatted' : 'Show JSON'}
                                </button>
                                <Text className={styles.sectionTitle}>
                                    <Icon iconName="Lightbulb" className={styles.sectionIcon} />
                                    Thought Process
                                </Text>
                            </Stack>
                            {showJsonView ? (
                                <div>
                                    {response.thought_process.map((step: ThoughtStep, index: number) => (
                                        <div key={index} style={{ marginBottom: '16px' }}>
                                            <strong>{step.step}:</strong>
                                            <pre style={{ 
                                                whiteSpace: 'pre-wrap', 
                                                marginTop: '8px',
                                                backgroundColor: '#f5f5f5',
                                                padding: '12px',
                                                borderRadius: '4px',
                                                fontSize: '12px',
                                                overflow: 'auto',
                                                maxHeight: '300px',
                                                border: '1px solid #ddd'
                                            }}>
                                                {JSON.stringify(step.details, null, 2)}
                                            </pre>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className={styles.timeline}>
                                    {response.thought_process.map((step: ThoughtStep, index: number) => (
                                        <div key={index} className={styles.timelineItem}>
                                            <div className={styles.timelineDot} />
                                            <div className={styles.timelineContent}>
                                                <Text className={styles.stepLabel}>
                                                    {formatStepName(step.step)}
                                                </Text>
                                                <Text className={styles.stepDetails}>
                                                    {formatStepDetails(step)}
                                                </Text>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Search History Section */}
                    {response.search_history && response.search_history.length > 0 && (
                        <div className={styles.section}>
                            <Text className={styles.sectionTitle}>
                                <Icon iconName="Search" className={styles.sectionIcon} />
                                Search History
                            </Text>
                            <div className={styles.searchList}>
                                {response.search_history.map((attempt: SearchAttempt, index: number) => (
                                    <div key={index} className={styles.searchItem}>
                                        <span className={styles.attemptNumber}>
                                            #{attempt.attempt}
                                        </span>
                                        <span className={styles.searchQuery}>
                                            "{attempt.query}"
                                        </span>
                                        <span className={styles.resultCount}>
                                            {attempt.results_count} results
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Decisions Section */}
                    {response.decisions && response.decisions.length > 0 && (
                        <div className={styles.section}>
                            <Text className={styles.sectionTitle}>
                                <Icon iconName="DecisionSolid" className={styles.sectionIcon} />
                                Reflection Decisions
                            </Text>
                            <div className={styles.decisionList}>
                                {response.decisions.map((decision: string, index: number) => (
                                    <div key={index} className={styles.decisionItem}>
                                        <Icon 
                                            iconName={decision.toLowerCase().includes("finalize") ? "CheckMark" : "Sync"} 
                                            className={`${styles.decisionIcon} ${
                                                decision.toLowerCase().includes("finalize") 
                                                    ? styles.decisionFinalize 
                                                    : styles.decisionRetry
                                            }`}
                                        />
                                        <span className={styles.decisionText}>{decision}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

/**
 * Format step name for display
 */
function formatStepName(step: string): string {
    const stepNames: Record<string, string> = {
        "retrieve": "Retrieve Documents",
        "review": "Review Results",
        "response": "Generate Response",
        "rewrite": "Rewrite Query",
        "reflect": "Reflect on Results"
    };
    return stepNames[step.toLowerCase()] || step.charAt(0).toUpperCase() + step.slice(1);
}

/**
 * Format step details for display
 */
function formatStepDetails(step: ThoughtStep): string {
    const details = step.details;
    
    if (!details || Object.keys(details).length === 0) {
        return "Completed";
    }

    // Handle common detail patterns
    if (details.results_count !== undefined) {
        return `Found ${details.results_count} results`;
    }
    if (details.valid_count !== undefined) {
        return `${details.valid_count} valid documents`;
    }
    if (details.final_answer) {
        return "Answer generated";
    }
    if (details.rewritten_query) {
        return `Rewrote to: "${details.rewritten_query}"`;
    }
    if (details.decision) {
        return `Decision: ${details.decision}`;
    }

    // Fallback: show first key-value pair
    const firstKey = Object.keys(details)[0];
    const firstValue = details[firstKey];
    if (typeof firstValue === "string" || typeof firstValue === "number") {
        return `${firstKey}: ${firstValue}`;
    }

    return "Completed";
}
