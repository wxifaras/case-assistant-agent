"""
Unified settings loader with Pydantic validation.

Loads configuration from (highest → lowest priority):
1. Constructor kwargs (test overrides)
2. Environment variables
3. Azure App Configuration  (when APP_CONFIG_ENDPOINT is set)
4. .env file (located in backend/.env)
5. File secrets directory

Bootstrap variables (always read from env / .env, never from App Config):
    APP_CONFIG_ENDPOINT          Azure App Configuration store endpoint URL
    APP_CONFIG_KEY_FILTER        Key prefix in the store (e.g. 'knowledge-assistant:*')
    APP_CONFIG_LABEL_FILTER      Environment label (e.g. 'production'); falls back to ENVIRONMENT
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.app_config_source import AppConfigAwareSettings
from app.models.config_options import (
    AIServicesOptions,
    APIOptions,
    AppConfigurationOptions,
    ApplicationInsightsOptions,
    AzureAIFoundryOptions,
    AzureOpenAIOptions,
    BlobStorageOptions,
    CosmosDBOptions,
    KeyVaultOptions,
    PIIDetectionOptions,
    SearchServiceOptions,
    WorkflowOptions,
)


class SearchServiceSettings(AppConfigAwareSettings):
    """Azure AI Search settings for indexing and retrieval."""

    model_config = SettingsConfigDict(
        env_prefix="SEARCHSERVICE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    endpoint: str = Field(..., description="Azure AI Search service endpoint URL")
    api_key: str = Field(..., description="Azure AI Search admin API key")
    skillset_api_version: str = Field(default="2025-08-01-preview", description="Preview API version for skillset")
    index_name: str = Field(default="knowledge-assistant-content-index", description="Name of the search index")
    data_source_name: str = Field(
        default="knowledge-assistant-content-datasource", description="Name of the data source connection"
    )
    skillset_name: str = Field(default="knowledge-assistant-content-skillset", description="Name of the skillset")
    indexer_name: str = Field(default="knowledge-assistant-content-indexer", description="Name of the indexer")
    markdown_skillset_name: str = Field(..., description="Name of the markdown parsing skillset")
    markdown_indexer_name: str = Field(..., description="Name of the markdown parsing indexer")
    json_skillset_name: str = Field(..., description="Name of the JSON parsing skillset")
    json_indexer_name: str = Field(..., description="Name of the JSON parsing indexer")
    min_reranker_score: float = Field(
        default=2.0, description="Minimum reranker score to retain a result when semantic ranking is enabled (0-4)"
    )


class BlobStorageSettings(AppConfigAwareSettings):
    """Azure Blob Storage settings for document storage."""

    model_config = SettingsConfigDict(
        env_prefix="BLOBSTORAGE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    resource_id: str | None = Field(None, description="Azure Storage Account Resource ID for managed identity")
    account_url: str | None = Field(None, description="Azure Storage Account endpoint URL for managed identity auth")
    connection_string: str | None = Field(None, description="Connection string for key-based authentication")
    container_name: str = Field(default="documents", description="Blob container name for source documents")
    images_container_name: str = Field(
        default="normalized-images", description="Blob container name for normalized images"
    )


class AIServicesSettings(AppConfigAwareSettings):
    """Azure AI Services settings."""

    model_config = SettingsConfigDict(
        env_prefix="AISERVICES_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    cognitive_services_endpoint: str = Field(..., description="Azure AI Services endpoint URL")
    cognitive_services_key: str | None = Field(None, description="API key for Azure AI Services")


class AzureOpenAISettings(AppConfigAwareSettings):
    """Azure OpenAI settings for embeddings and chat completions."""

    model_config = SettingsConfigDict(
        env_prefix="AZURE_OPENAI_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    endpoint: str = Field(..., description="Azure OpenAI endpoint URL")
    api_key: str | None = Field(None, description="API key for Azure OpenAI")
    deployment_name: str = Field(default="gpt-4", description="GPT-4 deployment name")
    embedding_deployment_name: str = Field(default="text-embedding-3-large", description="Embedding model deployment")
    api_version: str = Field(default="2025-04-01-preview", description="Azure OpenAI API version")

    # Separate endpoint for chat completion
    chat_completion_resource_uri: str | None = Field(None, description="Optional separate endpoint for chat completion")
    chat_completion_api_key: str | None = Field(None, description="Optional separate API key for chat completion")

    # Separate endpoint for vision/image verbalization in the skillset. Falls back to chat_completion_resource_uri if not set.
    image_chat_completion_resource_uri: str | None = Field(
        None,
        description="Optional endpoint for image verbalization in the skillset. Falls back to chat_completion_resource_uri if not set.",
    )

    # Model parameters
    temperature: float = Field(default=0.0, description="Temperature for LLM calls")
    max_tokens: int = Field(default=4096, description="Maximum tokens for response")
    max_context_tokens: int = Field(default=128000, description="Maximum context window size")


class CosmosDBSettings(AppConfigAwareSettings):
    """Azure Cosmos DB settings for conversation history."""

    model_config = SettingsConfigDict(env_prefix="COSMOS_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    endpoint: str | None = Field(
        None,
        description="Cosmos DB endpoint URL (e.g., https://<account>.documents.azure.com:443/) - used with managed identity",
    )
    connection_string: str | None = Field(
        None,
        description="Connection string for dev/test only (e.g., AccountEndpoint=...;AccountKey=...;) - not recommended for production",
    )
    database_name: str = Field(default="agentic_rag", description="Database name")
    container_name: str = Field(default="conversations", description="Container name for conversations")
    enable_ttl: bool = Field(default=True, description="Enable time-to-live for conversations")
    default_ttl_days: int = Field(default=30, description="Default TTL in days")


class AppConfigurationSettings(BaseSettings):
    """Azure App Configuration settings.

    Env-var prefix: ``APP_CONFIG_``

    Key environment variables:
        APP_CONFIG_ENABLED                   - enable the integration (default: false)
        APP_CONFIG_ENDPOINT                  - store endpoint URL (required to enable)
        APP_CONFIG_KEY_FILTER                - key prefix to load (e.g. 'knowledge-assistant:*')
        APP_CONFIG_LABEL_FILTER              - label selector (e.g. 'production')
        APP_CONFIG_REFRESH_ENABLED           - poll for changes (default: false)
        APP_CONFIG_REFRESH_INTERVAL_SECONDS  - polling interval in seconds (default: 30)
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_CONFIG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(
        default=False,
        description="Enable Azure App Configuration integration",
    )
    endpoint: str | None = Field(
        None,
        description="App Configuration store endpoint URL (required — used with DefaultAzureCredential)",
    )
    key_filter: str | None = Field(
        None,
        description="Key prefix filter — only keys with this prefix are loaded",
    )
    label_filter: str | None = Field(
        None,
        description="Label filter for environment-specific keys (e.g. 'production')",
    )
    refresh_enabled: bool = Field(
        default=False,
        description="Enable dynamic configuration refresh polling",
    )
    refresh_interval_seconds: int = Field(
        default=30,
        description="Polling interval in seconds when refresh is enabled",
    )


class KeyVaultSettings(AppConfigAwareSettings):
    """Azure Key Vault settings."""

    model_config = SettingsConfigDict(
        env_prefix="KEYVAULT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    url: str | None = Field(None, description="Key Vault URL")
    use_key_vault: bool = Field(default=False, description="Whether to use Key Vault for secrets")


class ApplicationInsightsSettings(AppConfigAwareSettings):
    """Application Insights settings."""

    model_config = SettingsConfigDict(
        env_prefix="APPINSIGHTS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    connection_string: str | None = Field(None, description="Application Insights connection string")
    enabled: bool = Field(default=True, description="Enable Application Insights telemetry")


class WorkflowSettings(AppConfigAwareSettings):
    """Workflow execution settings."""

    model_config = SettingsConfigDict(
        env_prefix="WORKFLOW_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    max_retrieval_iterations: int = Field(default=3, description="Maximum number of search iterations")
    chat_history_window: int = Field(default=5, description="Number of recent messages to include as context")
    enable_query_rewriting: bool = Field(default=True, description="Enable HyDE query rewriting for semantic search")
    enable_reflection: bool = Field(default=True, description="Enable reflection agent for result quality assessment")
    reflection_high_validity_threshold: float = Field(
        default=0.8, description="Valid-result rate for high-validity override"
    )
    reflection_moderate_validity_threshold: float = Field(
        default=0.6, description="Valid-result rate for moderate-validity override"
    )
    reflection_moderate_validity_min_count: int = Field(
        default=3, description="Minimum valid results for moderate-validity override"
    )
    hyde_temperature: float = Field(default=0.3, description="Sampling temperature for HyDE query generation")
    hyde_max_tokens: int = Field(default=500, description="Maximum tokens for HyDE hypothetical passage generation")
    answer_temperature: float = Field(default=0.1, description="Sampling temperature for answer generation")


class PIIDetectionSettings(AppConfigAwareSettings):
    """Azure AI Language PII detection settings."""

    model_config = SettingsConfigDict(
        env_prefix="PII_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    endpoint: str | None = Field(None, description="Azure AI Language endpoint URL")
    api_key: str | None = Field(None, description="API key; managed identity used when empty")
    enabled: bool = Field(default=True, description="Enable PII scanning")
    mode: str = Field(default="redact", description="PII handling mode: block, redact, or detect")
    block_on_detection: bool = Field(default=True, description="Return refusal when PII is detected")
    redact_responses: bool = Field(default=False, description="Redact PII from final answers")
    language: str = Field(default="en", description="BCP-47 language code")
    categories_filter: list[str] | None = Field(default=None, description="Optional list of PII categories to include")
    min_confidence: float = Field(default=0.75, description="Confidence threshold (0-1)")


class SharePointSettings(AppConfigAwareSettings):
    """Microsoft Graph / SharePoint connector settings.

    Auth uses ``DefaultAzureCredential`` against the Microsoft Graph scope.
    The managed identity (or service principal) must have the Graph
    application permissions ``Sites.Read.All`` and ``Files.Read.All`` granted
    with admin consent.
    """

    model_config = SettingsConfigDict(
        env_prefix="SHAREPOINT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    graph_base_url: str = Field(
        default="https://graph.microsoft.com/v1.0",
        description="Microsoft Graph base URL",
    )
    graph_scope: str = Field(
        default="https://graph.microsoft.com/.default",
        description="OAuth scope for Graph token acquisition",
    )
    site_hostname: str | None = Field(
        default=None,
        description="Optional default SharePoint site hostname (for example: contoso.sharepoint.com)",
    )
    site_path: str | None = Field(
        default=None,
        description="Optional default SharePoint site path (for example: /sites/MySite)",
    )
    library_name: str | None = Field(
        default=None,
        description="Optional default document library display name (for example: Documents)",
    )
    default_blob_container: str | None = Field(
        default=None,
        description="Optional default destination blob container (falls back to BLOBSTORAGE_CONTAINER_NAME)",
    )
    max_files_per_run: int = Field(
        default=500,
        ge=1,
        description="Hard cap on number of files processed per sync request",
    )
    request_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Per-Graph-request timeout in seconds",
    )
    download_chunk_size_bytes: int = Field(
        default=1024 * 1024,
        ge=64 * 1024,
        description="Chunk size for streamed SharePoint download/upload transfer",
    )


class APISettings(AppConfigAwareSettings):
    """API server settings."""

    model_config = SettingsConfigDict(env_prefix="API_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = Field(default="0.0.0.0", description="API host")
    port: int = Field(default=8000, description="API port")
    rate_limit_per_minute: int = Field(default=60, description="Rate limit per user per minute")
    enable_auth: bool = Field(default=False, description="Enable authentication")

    # CORS settings
    enable_cors: bool = Field(default=True, description="Enable CORS (Cross-Origin Resource Sharing)")

    # API documentation
    enable_docs: bool = Field(default=True, description="Enable Swagger/OpenAPI documentation endpoints")


class Settings(AppConfigAwareSettings):
    """Unified application settings loaded from environment variables or a ``.env`` file.

    Sub-settings classes (e.g. ``SearchServiceSettings``) are automatically
    populated from their prefixed env-vars when ``Settings`` is instantiated.
    Access options objects via the typed ``*_options`` properties which map
    nested settings to the ``BaseModel`` types consumed by the service layer.
    """

    # Azure Services
    search_service: SearchServiceSettings = Field(default_factory=lambda: SearchServiceSettings())  # type: ignore[call-arg]
    blob_storage: BlobStorageSettings = Field(default_factory=lambda: BlobStorageSettings())  # type: ignore[call-arg]
    ai_services: AIServicesSettings = Field(default_factory=lambda: AIServicesSettings())  # type: ignore[call-arg]
    azure_openai: AzureOpenAISettings = Field(default_factory=lambda: AzureOpenAISettings())  # type: ignore[call-arg]
    cosmos_db: CosmosDBSettings = Field(default_factory=lambda: CosmosDBSettings())  # type: ignore[call-arg]
    app_configuration: AppConfigurationSettings = Field(default_factory=lambda: AppConfigurationSettings())  # type: ignore[call-arg]
    key_vault: KeyVaultSettings = Field(default_factory=lambda: KeyVaultSettings())  # type: ignore[call-arg]
    app_insights: ApplicationInsightsSettings = Field(default_factory=lambda: ApplicationInsightsSettings())  # type: ignore[call-arg]
    workflow: WorkflowSettings = Field(default_factory=lambda: WorkflowSettings())  # type: ignore[call-arg]
    pii_detection: PIIDetectionSettings = Field(default_factory=lambda: PIIDetectionSettings())  # type: ignore[call-arg]
    sharepoint: SharePointSettings = Field(default_factory=lambda: SharePointSettings())  # type: ignore[call-arg]

    # Application Settings
    environment: str = Field(default="development", description="Environment: development, staging, production")
    use_managed_identity: bool = Field(default=False, description="Use Azure Managed Identity for authentication")
    api: APISettings = Field(default_factory=APISettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def search_service_options(self) -> SearchServiceOptions:
        """Create SearchServiceOptions from nested settings."""
        return SearchServiceOptions(
            endpoint=self.search_service.endpoint,
            api_key=self.search_service.api_key,
            skillset_api_version=self.search_service.skillset_api_version,
            index_name=self.search_service.index_name,
            data_source_name=self.search_service.data_source_name,
            skillset_name=self.search_service.skillset_name,
            indexer_name=self.search_service.indexer_name,
            markdown_skillset_name=self.search_service.markdown_skillset_name,
            markdown_indexer_name=self.search_service.markdown_indexer_name,
            json_skillset_name=self.search_service.json_skillset_name,
            json_indexer_name=self.search_service.json_indexer_name,
            min_reranker_score=self.search_service.min_reranker_score,
        )

    @property
    def blob_storage_options(self) -> BlobStorageOptions:
        """Create BlobStorageOptions from nested settings."""
        return BlobStorageOptions(
            resource_id=self.blob_storage.resource_id,
            connection_string=self.blob_storage.connection_string,
            container_name=self.blob_storage.container_name,
            images_container_name=self.blob_storage.images_container_name,
        )

    @property
    def ai_services_options(self) -> AIServicesOptions:
        """Create AIServicesOptions from nested settings."""
        return AIServicesOptions(
            cognitive_services_endpoint=self.ai_services.cognitive_services_endpoint,
            cognitive_services_key=self.ai_services.cognitive_services_key,
        )

    @property
    def azure_openai_options(self) -> AzureOpenAIOptions:
        """Create AzureOpenAIOptions from nested settings."""
        return AzureOpenAIOptions(
            resource_uri=self.azure_openai.endpoint,
            api_key=self.azure_openai.api_key,
            text_embedding_model=self.azure_openai.embedding_deployment_name,
            chat_completion_model=self.azure_openai.deployment_name,
            chat_completion_resource_uri=self.azure_openai.chat_completion_resource_uri,
            chat_completion_api_key=self.azure_openai.chat_completion_api_key,
            image_chat_completion_resource_uri=self.azure_openai.image_chat_completion_resource_uri,
        )

    @property
    def azure_ai_foundry_options(self) -> AzureAIFoundryOptions:
        """Create AzureAIFoundryOptions from consolidated OpenAI settings."""
        return AzureAIFoundryOptions(
            endpoint=self.azure_openai.endpoint,
            api_key=self.azure_openai.api_key,
            deployment_name=self.azure_openai.deployment_name,
            embedding_deployment_name=self.azure_openai.embedding_deployment_name,
            api_version=self.azure_openai.api_version,
            temperature=self.azure_openai.temperature,
            max_tokens=self.azure_openai.max_tokens,
            max_context_tokens=self.azure_openai.max_context_tokens,
        )

    @property
    def cosmos_db_options(self) -> CosmosDBOptions:
        """Create CosmosDBOptions from nested settings."""
        return CosmosDBOptions(
            endpoint=self.cosmos_db.endpoint,
            connection_string=self.cosmos_db.connection_string,
            database_name=self.cosmos_db.database_name,
            container_name=self.cosmos_db.container_name,
            enable_ttl=self.cosmos_db.enable_ttl,
            default_ttl_days=self.cosmos_db.default_ttl_days,
        )

    @property
    def app_configuration_options(self) -> AppConfigurationOptions:
        """Create AppConfigurationOptions from nested settings."""
        return AppConfigurationOptions(
            enabled=self.app_configuration.enabled,
            endpoint=self.app_configuration.endpoint,
            key_filter=self.app_configuration.key_filter,
            label_filter=self.app_configuration.label_filter,
            refresh_enabled=self.app_configuration.refresh_enabled,
            refresh_interval_seconds=self.app_configuration.refresh_interval_seconds,
        )

    @property
    def key_vault_options(self) -> KeyVaultOptions:
        """Create KeyVaultOptions from nested settings."""
        return KeyVaultOptions(
            url=self.key_vault.url,
            use_key_vault=self.key_vault.use_key_vault,
        )

    @property
    def app_insights_options(self) -> ApplicationInsightsOptions:
        """Create ApplicationInsightsOptions from nested settings."""
        return ApplicationInsightsOptions(
            connection_string=self.app_insights.connection_string,
            enabled=self.app_insights.enabled,
        )

    @property
    def workflow_options(self) -> WorkflowOptions:
        """Create WorkflowOptions from nested settings."""
        return WorkflowOptions(
            max_retrieval_iterations=self.workflow.max_retrieval_iterations,
            chat_history_window=self.workflow.chat_history_window,
            enable_query_rewriting=self.workflow.enable_query_rewriting,
            enable_reflection=self.workflow.enable_reflection,
            reflection_high_validity_threshold=self.workflow.reflection_high_validity_threshold,
            reflection_moderate_validity_threshold=self.workflow.reflection_moderate_validity_threshold,
            reflection_moderate_validity_min_count=self.workflow.reflection_moderate_validity_min_count,
            hyde_temperature=self.workflow.hyde_temperature,
            hyde_max_tokens=self.workflow.hyde_max_tokens,
            answer_temperature=self.workflow.answer_temperature,
        )

    @property
    def pii_detection_options(self) -> PIIDetectionOptions:
        """Create PIIDetectionOptions from nested settings."""
        return PIIDetectionOptions(
            endpoint=self.pii_detection.endpoint,
            api_key=self.pii_detection.api_key,
            enabled=self.pii_detection.enabled,
            block_on_detection=self.pii_detection.block_on_detection,
            min_confidence=self.pii_detection.min_confidence,
            language=self.pii_detection.language,
            redact_responses=self.pii_detection.redact_responses,
        )

    @property
    def api_options(self) -> APIOptions:
        """Create APIOptions from nested settings."""
        return APIOptions(
            host=self.api.host,
            port=self.api.port,
            rate_limit_per_minute=self.api.rate_limit_per_minute,
            enable_auth=self.api.enable_auth,
            enable_cors=self.api.enable_cors,
            enable_docs=self.api.enable_docs,
        )


# Singleton instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the module-level ``Settings`` singleton, creating it on first call.

    Returns:
        The shared ``Settings`` instance populated from env-vars / ``.env``.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
