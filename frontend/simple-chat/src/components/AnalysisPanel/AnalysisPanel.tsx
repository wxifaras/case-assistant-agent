import { Pivot, PivotItem } from "@fluentui/react";
import DOMPurify from "dompurify";

import styles from "./AnalysisPanel.module.css";

import { SupportingContent } from "../SupportingContent";
import { UnifiedChatResponse } from "../../api";
import { AnalysisPanelTabs } from "./AnalysisPanelTabs";

interface Props {
    className: string;
    activeTab: AnalysisPanelTabs;
    onActiveTabChanged: (tab: AnalysisPanelTabs) => void;
    activeCitation: string | undefined;
    citationHeight: string;
    answer: UnifiedChatResponse;
}

const pivotItemDisabledStyle = { disabled: true, style: { color: "grey" } };

export const AnalysisPanel = ({ answer, activeTab, activeCitation, citationHeight, className, onActiveTabChanged }: Props) => {
    const isDisabledSupportingContentTab = answer.citations.length === 0;
    const isDisabledCitationTab: boolean = !activeCitation;

    // Convert citations to supporting content format
    const supportingContent = answer.citations.map(c => 
        `[${c.document_title || c.content_id}${c.page_number ? ` (p.${c.page_number})` : ''}]: ${c.content}`
    );

    // Find the specific citation object that matches the activeCitation ID
    const selectedCitation = activeCitation 
        ? answer.citations.find(c => c.content_id === activeCitation || c.content_path === activeCitation)
        : undefined;

    return (
        <Pivot
            className={className}
            selectedKey={activeTab}
            onLinkClick={pivotItem => pivotItem && onActiveTabChanged(pivotItem.props.itemKey! as AnalysisPanelTabs)}
        >
            <PivotItem
                itemKey={AnalysisPanelTabs.SupportingContentTab}
                headerText="Supporting content"
                headerButtonProps={isDisabledSupportingContentTab ? pivotItemDisabledStyle : undefined}
            >
                <SupportingContent supportingContent={supportingContent} />
            </PivotItem>
            <PivotItem
                itemKey={AnalysisPanelTabs.CitationTab}
                headerText="Citation"
                headerButtonProps={isDisabledCitationTab ? pivotItemDisabledStyle : undefined}
            >
                {selectedCitation ? (
                    <div className={styles.citationContent}>
                        <h3>{selectedCitation.document_title || selectedCitation.content_id}</h3>
                        {selectedCitation.page_number && (
                            <p style={{ fontStyle: 'italic', marginBottom: '16px' }}>
                                Page {selectedCitation.page_number}
                            </p>
                        )}
                        {selectedCitation.relevance_score && (
                            <p style={{ fontSize: '12px', color: '#666', marginBottom: '16px' }}>
                                Relevance Score: {selectedCitation.relevance_score.toFixed(2)}
                            </p>
                        )}
                        <div style={{ 
                            padding: '16px', 
                            backgroundColor: '#f5f5f5', 
                            borderRadius: '4px',
                            whiteSpace: 'pre-wrap',
                            lineHeight: '1.6',
                            fontSize: '14px'
                        }}>
                            {selectedCitation.content}
                        </div>
                    </div>
                ) : (
                    <div>No citation selected</div>
                )}
            </PivotItem>
        </Pivot>
    );
};
