const config = {
  botSsoConnectionName: process.env.BOT_SSO_CONNECTION_NAME || "SSOSelf",
  agentUrl: process.env.AGENT_URL || "http://localhost:8000",
};

export default config;
