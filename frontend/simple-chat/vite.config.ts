import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { DefaultAzureCredential } from "@azure/identity";

// AAD scope for Azure AI Foundry / Azure OpenAI data-plane access.
const FOUNDRY_AAD_SCOPE = "https://cognitiveservices.azure.com/.default";

// https://vitejs.dev/config/
export default defineConfig(async ({ mode }) => {
    // Load all env vars (including non-VITE_ secrets) for use in the dev proxy.
    const env = loadEnv(mode, process.cwd(), "");

    const foundryEndpoint = (env.FOUNDRY_ENDPOINT || "").replace(/\/+$/, "");
    const foundryPathPrefix = env.FOUNDRY_PATH_PREFIX || "/openai/v1";
    const foundryApiKey = env.FOUNDRY_API_KEY || "";

    // Acquire AAD tokens using the local developer identity (the account from
    // `az login`, VS Code, env vars, etc.). The token is cached and refreshed in
    // the background so the (synchronous) proxy request handler can read it
    // without awaiting — http-proxy's `proxyReq` event does not wait for promises.
    const credential = new DefaultAzureCredential();
    let cachedToken: string | undefined;
    let cachedExpiresOn = 0;
    let refreshing = false;

    const refreshToken = async (): Promise<void> => {
        if (refreshing) return;
        refreshing = true;
        try {
            const token = await credential.getToken(FOUNDRY_AAD_SCOPE);
            if (token?.token) {
                cachedToken = token.token;
                cachedExpiresOn = token.expiresOnTimestamp;
            }
        } catch (err) {
            console.error("[foundry-proxy] Failed to acquire AAD token via DefaultAzureCredential:", err);
        } finally {
            refreshing = false;
        }
    };

    // Kick off (background) refresh when the cached token is missing or within
    // 5 minutes of expiry. Does not block the request.
    const ensureFreshToken = (): void => {
        if (!cachedToken || Date.now() >= cachedExpiresOn - 5 * 60 * 1000) {
            void refreshToken();
        }
    };

    // Prime the cache once at startup (only when an endpoint is configured) so
    // the very first proxied request already has a token.
    if (foundryEndpoint) {
        await refreshToken();
    }

    return {
        plugins: [react()],
        build: {
            outDir: "./build",
            emptyOutDir: true,
            sourcemap: true,
            rollupOptions: {
                output: {
                    manualChunks: id => {
                        if (id.includes("@fluentui/react-icons")) {
                            return "fluentui-icons";
                        } else if (id.includes("@fluentui/react")) {
                            return "fluentui-react";
                        } else if (id.includes("node_modules")) {
                            return "vendor";
                        }
                    }
                }
            },
            target: "esnext"
        },
        server: {
            proxy: {
                "/api": {
                    target: "http://localhost:8000",
                    changeOrigin: true
                },
                // Secure server-side proxy to the Foundry Responses API.
                // Auth is injected here so no secret/token ships in the browser bundle.
                // Preferred: an AAD bearer token from the local developer identity
                // (whoever ran `az login`). Falls back to FOUNDRY_API_KEY if set and
                // no token could be acquired.
                "/foundry": {
                    target: foundryEndpoint || "http://localhost:8000",
                    changeOrigin: true,
                    secure: true,
                    rewrite: path => path.replace(/^\/foundry/, foundryPathPrefix),
                    configure: proxy => {
                        proxy.on("proxyReq", proxyReq => {
                            ensureFreshToken();
                            if (cachedToken) {
                                proxyReq.setHeader("Authorization", `Bearer ${cachedToken}`);
                            } else if (foundryApiKey) {
                                proxyReq.setHeader("api-key", foundryApiKey);
                            } else {
                                console.error(
                                    "[foundry-proxy] No AAD token and no FOUNDRY_API_KEY; request will be unauthenticated. " +
                                        "Run `az login` (with access to the Foundry resource) or set FOUNDRY_API_KEY."
                                );
                            }
                        });
                    }
                }
            }
        }
    };
});

