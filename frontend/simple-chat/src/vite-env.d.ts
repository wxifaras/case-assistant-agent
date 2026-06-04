/// <reference types="vite/client" />

interface ImportMetaEnv {
    /** Legacy backend URI (unused by the Foundry conversational flow). */
    readonly VITE_BACKEND_URI?: string;
    /** Foundry agent name / id (or model deployment) to prompt. */
    readonly VITE_FOUNDRY_AGENT?: string;
    /** Optional api-version query string for the Responses API. */
    readonly VITE_FOUNDRY_API_VERSION?: string;
    /** Comma-separated SharePoint site display names for the dropdown. */
    readonly VITE_SHAREPOINT_SITES?: string;
}

interface ImportMeta {
    readonly env: ImportMetaEnv;
}
