import styles from "./Example.module.css";

const EXAMPLE_QUESTIONS: string[] = [
    "How can I reset my password?",
    "I am having trouble with Teams",
    "How do I connect to the VPN?",
    "My laptop won't turn on, what should I do?"
];

interface ExampleListProps {
    /** Called when the user clicks one of the canned example questions. */
    onExampleClicked: (question: string) => void;
    /** Disable interaction while a request is in flight. */
    disabled?: boolean;
}

/**
 * Grid of canned starter questions shown in the empty chat state so users
 * have a quick way to try the assistant without typing.
 */
export const ExampleList = ({ onExampleClicked, disabled = false }: ExampleListProps): JSX.Element => {
    return (
        <ul className={styles.examplesContainer} aria-label="Example questions">
            {EXAMPLE_QUESTIONS.map((question, i) => (
                <li key={i} style={{ listStyle: "none" }}>
                    <button
                        type="button"
                        className={styles.example}
                        disabled={disabled}
                        onClick={() => onExampleClicked(question)}
                    >
                        {question}
                    </button>
                </li>
            ))}
        </ul>
    );
};
