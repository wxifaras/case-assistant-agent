import { TextField } from "@fluentui/react";
import styles from "./UserIdInput.module.css";

interface UserIdInputProps {
    /** Current user ID value */
    userId: string;
    /** Callback when user ID changes */
    onUserIdChange: (userId: string) => void;
    /** Whether the input should be disabled */
    disabled?: boolean;
    /** Optional CSS class name */
    className?: string;
}

/**
 * Input component for manually entering a user ID during development/testing.
 * 
 * NOTE: This is a temporary component for dev/test purposes only.
 * In production, user_id should be derived from MSAL authentication token claims.
 * See todo.md for implementation details.
 */
export const UserIdInput = ({ 
    userId, 
    onUserIdChange, 
    disabled = false,
    className 
}: UserIdInputProps): JSX.Element => {
    const handleChange = (_: React.FormEvent<HTMLInputElement | HTMLTextAreaElement>, newValue?: string) => {
        onUserIdChange(newValue || "");
    };

    return (
        <div className={`${styles.container} ${className || ""}`}>
            <TextField
                className={styles.input}
                placeholder="User ID (dev/test)"
                value={userId}
                onChange={handleChange}
                disabled={disabled}
                borderless
                underlined
                title="Enter a user ID for conversation tracking (development/testing only)"
            />
        </div>
    );
};
