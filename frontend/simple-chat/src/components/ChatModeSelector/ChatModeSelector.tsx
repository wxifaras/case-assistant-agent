import { ChoiceGroup, IChoiceGroupOption } from "@fluentui/react";
import { ChatMode } from "../../api";
import styles from "./ChatModeSelector.module.css";

interface ChatModeSelectorProps {
    /** Currently selected chat mode */
    selectedMode: ChatMode;
    /** Callback when mode changes */
    onModeChange: (mode: ChatMode) => void;
    /** Whether the selector should be disabled (e.g., conversation started) */
    disabled?: boolean;
}

const modeOptions: IChoiceGroupOption[] = [
    {
        key: ChatMode.Standard,
        text: "Standard Chat",
        iconProps: { iconName: "Chat" },
        styles: {
            root: { marginBottom: 4 }
        }
    },
    {
        key: ChatMode.Agentic,
        text: "Agentic RAG",
        iconProps: { iconName: "ChatBot" },
        styles: {
            root: { marginBottom: 4 }
        }
    }
];

/**
 * Component for selecting the chat mode (Standard or Agentic RAG).
 * Should be displayed in the empty state before a conversation begins.
 * 
 * - Standard Chat: Simple Q&A using /api/chat endpoint
 * - Agentic RAG: Multi-step reasoning with query rewriting, reflection,
 *   and detailed workflow metadata using /api/chat/query endpoint
 */
export const ChatModeSelector = ({
    selectedMode,
    onModeChange,
    disabled = false
}: ChatModeSelectorProps): JSX.Element => {
    const handleChange = (
        _ev?: React.FormEvent<HTMLElement | HTMLInputElement>,
        option?: IChoiceGroupOption
    ) => {
        if (option) {
            onModeChange(option.key as ChatMode);
        }
    };

    return (
        <div className={styles.container}>
            <ChoiceGroup
                className={styles.choiceGroup}
                selectedKey={selectedMode}
                options={modeOptions}
                onChange={handleChange}
                disabled={disabled}
                label="Choose your chat mode:"
            />
            <div className={styles.description}>
                {selectedMode === ChatMode.Standard ? (
                    <p className={styles.modeDescription}>
                        <strong>Standard Chat</strong> provides quick answers with source citations.
                        Best for straightforward questions.
                    </p>
                ) : (
                    <p className={styles.modeDescription}>
                        <strong>Agentic RAG</strong> uses multi-step reasoning with query rewriting 
                        and reflection. Shows the AI's thought process. Best for complex questions.
                    </p>
                )}
            </div>
        </div>
    );
};
