"""Configuration options for all Azure services and application settings."""

import re

from pydantic import BaseModel, Field, field_validator


class SearchServiceOptions(BaseModel):
    """Configuration for Azure AI Search service.

    Supports two authentication methods:
    1. API Key: Provide api_key for key-based authentication
    2. Managed Identity: Leave api_key empty/None to use DefaultAzureCredential
    """

    endpoint: str = Field(..., description="Azure AI Search service endpoint URL")
    api_key: str | None = Field(
        None, min_length=1, description="Azure AI Search admin API key - leave empty to use managed identity"
    )
    skillset_api_version: str = Field(..., description="Preview API version for skillset with GenAI Prompt skill")
    index_name: str = Field(..., min_length=1, description="Name of the search index")
    data_source_name: str = Field(..., min_length=1, description="Name of the data source connection")
    skillset_name: str = Field(..., min_length=1, description="Name of the skillset")
    indexer_name: str = Field(..., min_length=1, description="Name of the indexer")
    markdown_skillset_name: str = Field(
        default="", description="Name of the markdown parsing skillset (auto-derived if empty)"
    )
    markdown_indexer_name: str = Field(
        default="", description="Name of the markdown parsing indexer (auto-derived if empty)"
    )
    json_skillset_name: str = Field(default="", description="Name of the JSON parsing skillset (auto-derived if empty)")
    json_indexer_name: str = Field(default="", description="Name of the JSON parsing indexer (auto-derived if empty)")
    min_reranker_score: float = Field(
        default=2.0,
        ge=0.0,
        le=4.0,
        description="Minimum reranker score to retain a result when semantic ranking is enabled (Azure AI Search scores range from 0 to 4)",
    )

    def model_post_init(self, __context) -> None:
        """Auto-derive markdown/JSON pipeline names from the index name when not explicitly set."""
        base = self.index_name.rsplit("-index", 1)[0] if self.index_name.endswith("-index") else self.index_name
        if not self.markdown_skillset_name:
            self.markdown_skillset_name = f"{base}-markdown-skillset"
        if not self.markdown_indexer_name:
            self.markdown_indexer_name = f"{base}-markdown-indexer"
        if not self.json_skillset_name:
            self.json_skillset_name = f"{base}-json-skillset"
        if not self.json_indexer_name:
            self.json_indexer_name = f"{base}-json-indexer"

    @field_validator("skillset_api_version")
    @classmethod
    def validate_api_version(cls, v: str) -> str:
        """Validate API version format (YYYY-MM-DD or YYYY-MM-DD-preview)."""
        pattern: str = r"^\d{4}-\d{2}-\d{2}(-preview)?$"
        if not re.match(pattern, v):
            raise ValueError(f"Invalid API version format: {v}. Expected format: YYYY-MM-DD or YYYY-MM-DD-preview")
        return v

    model_config = {
        "str_strip_whitespace": True,
    }


class BlobStorageOptions(BaseModel):
    """Configuration for Azure Blob Storage.

    Supports two authentication methods:
    1. Connection string (key-based): Set connection_string
    2. Managed identity: Set resource_id (used with DefaultAzureCredential)
    """

    connection_string: str | None = Field(
        None, description="Azure Storage connection string (key-based authentication)"
    )
    resource_id: str | None = Field(
        None, description="Azure Storage Account Resource ID for managed identity authentication"
    )
    container_name: str = Field(..., description="Blob container name for source documents")
    images_container_name: str = Field(..., description="Blob container name for normalized images (knowledge store)")

    model_config = {
        "str_strip_whitespace": True,
    }


class AIServicesOptions(BaseModel):
    """Configuration for Azure AI Services (Cognitive Services).

    Supports two authentication methods:
    1. API key (key-based): Set cognitive_services_key
    2. Managed identity: Leave cognitive_services_key empty (uses DefaultAzureCredential)

    The endpoint is always required for both authentication methods.
    """

    cognitive_services_endpoint: str = Field(
        ..., description="Azure Cognitive Services endpoint URL (required for both auth methods)"
    )
    cognitive_services_key: str | None = Field(
        default=None,
        description="Azure Cognitive Services API key for key-based authentication (optional - if not provided, uses managed identity)",
    )

    model_config = {
        "str_strip_whitespace": True,
    }


class AzureOpenAIOptions(BaseModel):
    """Configuration for Azure OpenAI service.

    Supports two authentication methods:
    1. API key authentication: Set api_key and/or chat_completion_api_key
    2. Managed identity: Leave keys as None (uses DefaultAzureCredential)

    Supports separate or shared endpoints:
    - Separate: Configure chat_completion_resource_uri for chat, resource_uri for embeddings
    - Shared: Use only resource_uri for both services

    Fallback behavior:
    - If chat_completion_resource_uri is not provided, uses resource_uri for chat
    - If chat_completion_api_key is not provided, uses api_key for chat
    - If both api_key values are None, uses managed identity for authentication
    """

    resource_uri: str = Field(
        ...,
        description="Azure OpenAI service base endpoint URL for embeddings (e.g., https://<resource>.openai.azure.com/)",
    )
    api_key: str | None = Field(
        default=None,
        description="Azure OpenAI API key for embeddings (also used for chat if chat_completion_api_key not provided). Optional - if not provided, uses managed identity.",
    )
    text_embedding_model: str = Field(
        ..., min_length=1, description="Text embedding model deployment name (e.g., text-embedding-3-large)"
    )
    chat_completion_model: str = Field(
        ..., min_length=1, description="Chat completion model deployment name (e.g., gpt-4o)"
    )
    chat_completion_resource_uri: str | None = Field(
        default=None,
        description="Optional separate endpoint for chat completion (e.g., https://<resource>.openai.azure.com/openai/deployments/<model>/chat/completions?api-version=2025-01-01-preview). Falls back to resource_uri if not provided.",
    )
    chat_completion_api_key: str | None = Field(
        default=None,
        description="Optional separate API key for chat completion. Falls back to api_key if not provided. Supports managed identity if both are None.",
    )
    image_chat_completion_resource_uri: str | None = Field(
        default=None,
        description="Optional separate endpoint for image verbalization in the skillset. Falls back to chat_completion_resource_uri if not set.",
    )
    max_context_tokens: int = Field(
        default=128000,
        gt=0,
        description="Maximum input context window for the chat completion model. Set this to match your deployment's context limit (e.g. 128000 for gpt-4o standard deployments).",
    )

    model_config = {
        "str_strip_whitespace": True,
    }

    @property
    def effective_chat_completion_uri(self) -> str:
        """Get the effective URI for chat completion (uses override if available)."""
        return self.chat_completion_resource_uri or self.resource_uri

    @property
    def effective_image_uri(self) -> str:
        """Get the effective URI for image verbalization in the skillset.

        Resolution order:
        1. image_chat_completion_resource_uri   (specific override for image verbalization)
        2. chat_completion_resource_uri         (shared chat endpoint)
        3. resource_uri                         (base OpenAI endpoint)
        """
        return self.image_chat_completion_resource_uri or self.chat_completion_resource_uri or self.resource_uri

    @property
    def effective_chat_completion_key(self) -> str | None:
        """Get the effective API key for chat completion (uses override if available). Returns None for managed identity."""
        return self.chat_completion_api_key or self.api_key


class AzureAIFoundryOptions(BaseModel):
    """Configuration for Azure AI Foundry service.

    Supports two authentication methods:
    1. API key authentication: Set api_key
    2. Managed identity: Leave api_key as None (uses DefaultAzureCredential)
    """

    endpoint: str | None = Field(
        None, description="Azure AI Foundry endpoint URL (e.g., https://<project>.services.ai.azure.com)"
    )
    api_key: str | None = Field(
        None, description="API key for Azure AI Foundry. Optional - if not provided, uses managed identity."
    )
    deployment_name: str = Field(default="gpt-4", description="GPT-4 deployment name")
    embedding_deployment_name: str = Field(
        default="text-embedding-3-large", description="Embedding model deployment name"
    )
    api_version: str = Field(default="2024-12-01-preview", description="Azure OpenAI API version")

    # Model parameters
    temperature: float = Field(
        default=0.0, ge=0.0, le=2.0, description="Temperature for LLM calls (0 for deterministic, higher for creative)"
    )
    max_tokens: int = Field(default=4096, gt=0, description="Maximum tokens for response generation")
    max_context_tokens: int = Field(default=128000, gt=0, description="Maximum context window size")

    model_config = {
        "str_strip_whitespace": True,
    }


class FoundryAgentOptions(BaseModel):
    """Configuration for runtime invocation of a Foundry prompt agent."""

    enabled: bool = Field(default=False, description="Enable Foundry prompt-agent invocation path")
    project_endpoint: str | None = Field(
        default=None,
        description="Foundry project endpoint URL (recommended format: https://<account>.services.ai.azure.com/api/projects/<project>)",
    )
    agent_name: str | None = Field(default=None, description="Foundry prompt-agent name")
    model: str | None = Field(default=None, description="Model deployment name used by the prompt agent")
    timeout_seconds: int = Field(default=90, gt=0, description="Timeout in seconds for a Foundry prompt-agent run")

    model_config = {
        "str_strip_whitespace": True,
    }


class CosmosDBOptions(BaseModel):
    """Configuration for Azure Cosmos DB for conversation history storage."""

    endpoint: str | None = Field(
        None,
        description="Cosmos DB endpoint URL (e.g., https://<account>.documents.azure.com:443/) - used with managed identity",
    )
    connection_string: str | None = Field(
        None, description="Cosmos DB connection string. Only use in dev/test - not recommended for production."
    )
    database_name: str = Field(
        default="case-assistant-db", min_length=1, description="Database name for storing conversations"
    )
    container_name: str = Field(
        default="conversations", min_length=1, description="Container name for conversation history"
    )
    sites_container_name: str = Field(
        default="sites", min_length=1, description="Container name for SharePoint sites and sync state"
    )
    enable_ttl: bool = Field(default=True, description="Enable automatic time-to-live for conversation cleanup")
    default_ttl_days: int = Field(
        default=30, gt=0, le=365, description="Default TTL in days for conversation expiration"
    )

    @field_validator("default_ttl_days")
    @classmethod
    def validate_ttl_days(cls, v: int) -> int:
        """Validate TTL is within reasonable bounds."""
        if v < 1 or v > 365:
            raise ValueError("TTL days must be between 1 and 365")
        return v

    model_config = {
        "str_strip_whitespace": True,
    }


class AppConfigurationOptions(BaseModel):
    """Configuration for Azure App Configuration.

    Provides centralised, managed configuration with optional dynamic refresh
    and Key Vault reference resolution.

    Authentication methods:
    1. Managed identity (recommended for production): set ``endpoint`` only.

    Key Vault integration:
        Any value stored in App Configuration as a Key Vault reference
        (``{"uri": "https://<vault>.vault.azure.net/secrets/<name>"}``)
        is automatically resolved to the secret value using the same
        ``DefaultAzureCredential`` — no separate Key Vault URL is needed.
    """

    enabled: bool = Field(
        default=False,
        description="Enable Azure App Configuration integration. When True, settings are loaded from the remote store at startup.",
    )
    endpoint: str | None = Field(
        None,
        description="App Configuration store endpoint (e.g., https://<store>.azconfig.io). Required to enable — authenticated via DefaultAzureCredential.",
    )
    key_filter: str | None = Field(
        None,
        description="Key prefix filter. Only keys whose names start with this prefix are loaded (e.g., 'myapp/' loads 'myapp/setting1', 'myapp/setting2').",
    )
    label_filter: str | None = Field(
        None,
        description="Label filter for selecting environment-specific settings (e.g., 'production', 'staging'). Use '\\0' for no-label keys.",
    )
    refresh_enabled: bool = Field(
        default=False,
        description="Enable dynamic configuration refresh. When True, the client periodically polls App Configuration for changes.",
    )
    refresh_interval_seconds: int = Field(
        default=30,
        ge=1,
        description="How often (in seconds) to poll App Configuration for changes when refresh is enabled. Minimum: 1 second.",
    )

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str | None) -> str | None:
        """Validate App Configuration endpoint format if provided."""
        if v and not v.startswith("https://"):
            raise ValueError("App Configuration endpoint must start with https://")
        return v

    model_config = {
        "str_strip_whitespace": True,
    }


class KeyVaultOptions(BaseModel):
    """Configuration for Azure Key Vault for secrets management.

    When enabled, the application will retrieve secrets from Key Vault
    using managed identity authentication.
    """

    url: str | None = Field(None, description="Key Vault URL (e.g., https://<vault-name>.vault.azure.net/)")
    use_key_vault: bool = Field(default=False, description="Whether to use Key Vault for secrets retrieval")

    @field_validator("url")
    @classmethod
    def validate_url_format(cls, v: str | None) -> str | None:
        """Validate Key Vault URL format if provided."""
        if v and not v.startswith("https://"):
            raise ValueError("Key Vault URL must start with https://")
        if v and not v.endswith(".vault.azure.net/"):
            if not v.endswith(".vault.azure.net"):
                raise ValueError("Key Vault URL must end with .vault.azure.net/")
        return v

    model_config = {
        "str_strip_whitespace": True,
    }


class ApplicationInsightsOptions(BaseModel):
    """Configuration for Azure Application Insights telemetry.

    Application Insights provides monitoring, logging, and diagnostics
    for production applications.
    """

    connection_string: str | None = Field(
        None, description="Application Insights connection string (e.g., InstrumentationKey=...;IngestionEndpoint=...)"
    )
    enabled: bool = Field(default=True, description="Enable Application Insights telemetry collection")

    model_config = {
        "str_strip_whitespace": True,
    }


class WorkflowOptions(BaseModel):
    """Configuration for workflow execution.

    Controls execution limits and feature flags for the
    multi-agent retrieval-augmented generation workflow.
    """

    # Execution limits
    max_retrieval_iterations: int = Field(
        default=3, ge=1, le=10, description="Maximum number of search iterations (includes retries with reflection)"
    )
    chat_history_window: int = Field(
        default=5, ge=1, le=100, description="Number of recent conversation messages to include as context for the LLM"
    )

    # Query processing features
    enable_query_rewriting: bool = Field(default=True, description="Enable HyDE query rewriting for semantic search")
    enable_reflection: bool = Field(default=True, description="Enable reflection agent for result quality assessment")

    # Smart-retry thresholds for the reflection agent
    reflection_high_validity_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Valid-result rate above which 'finalize' is overridden to 'retry' (high hit-rate suggests more content exists)",
    )
    reflection_moderate_validity_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Valid-result rate above which 'finalize' is overridden to 'retry' when combined with a minimum count",
    )
    reflection_moderate_validity_min_count: int = Field(
        default=3,
        ge=1,
        description="Minimum number of valid results required for the moderate-validity override to trigger",
    )

    # HyDE query rewriting parameters
    hyde_temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for HyDE query generation (higher = more diverse rewrites)",
    )
    hyde_max_tokens: int = Field(
        default=500,
        ge=50,
        description="Maximum tokens for HyDE hypothetical passage generation",
    )

    # Answer generation parameters
    answer_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for answer generation (lower = more factual, grounded answers)",
    )

    model_config = {
        "str_strip_whitespace": True,
    }


class APIOptions(BaseModel):
    """Configuration for the FastAPI server.

    Controls server binding, rate limiting, and authentication settings.
    """

    host: str = Field(
        default="0.0.0.0", description="API server host (0.0.0.0 for all interfaces, 127.0.0.1 for localhost only)"
    )
    port: int = Field(default=8000, ge=1, le=65535, description="API server port number")
    rate_limit_per_minute: int = Field(
        default=60, ge=1, le=1000, description="Maximum requests per user per minute (rate limiting)"
    )
    enable_auth: bool = Field(default=False, description="Enable authentication and authorization for API endpoints")

    # CORS settings
    enable_cors: bool = Field(default=True, description="Enable CORS (Cross-Origin Resource Sharing)")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"], description="Allowed CORS origins"
    )

    # API documentation
    enable_docs: bool = Field(default=True, description="Enable Swagger/OpenAPI documentation endpoints")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port is within valid range."""
        if v < 1 or v > 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v

    @field_validator("rate_limit_per_minute")
    @classmethod
    def validate_rate_limit(cls, v: int) -> int:
        """Validate rate limit is reasonable."""
        if v < 1:
            raise ValueError("Rate limit must be at least 1 request per minute")
        if v > 1000:
            raise ValueError("Rate limit cannot exceed 1000 requests per minute")
        return v

    model_config = {
        "str_strip_whitespace": True,
    }


class PIIDetectionOptions(BaseModel):
    """Configuration for Azure AI Language PII detection and redaction."""

    endpoint: str | None = Field(
        None,
        description="Azure AI Language service endpoint URL",
    )
    api_key: str | None = Field(
        None,
        description="Azure AI Language API key. Leave empty to use managed identity.",
    )
    enabled: bool = Field(default=False, description="Enable PII scanning")

    # Preferred mode selector used by workflow executors.
    mode: str = Field(
        default="redact",
        description="PII handling mode: block, redact, or detect",
    )

    # Backward-compatible flags still used by ChatService.
    block_on_detection: bool = Field(
        default=False,
        description="When True, return refusal response if prompt contains PII",
    )
    redact_responses: bool = Field(
        default=False,
        description="When True, redact PII from generated answers",
    )

    language: str = Field(default="en", description="BCP-47 language code")
    categories_filter: list[str] | None = Field(
        default=None,
        description="Optional list of PII categories to include",
    )
    min_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score (0.0-1.0)",
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate supported mode values."""
        mode = (v or "").strip().lower()
        if mode not in {"block", "redact", "detect"}:
            raise ValueError("mode must be one of: block, redact, detect")
        return mode

    model_config = {
        "str_strip_whitespace": True,
    }
