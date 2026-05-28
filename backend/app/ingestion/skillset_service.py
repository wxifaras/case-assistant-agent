"""Skillset service for Azure AI Search."""

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any

import httpx
from azure.search.documents.indexes.aio import SearchIndexerClient
from azure.search.documents.indexes.models import (
    AIServicesAccountIdentity,
    AzureOpenAIEmbeddingSkill,
    ChatCompletionSkill,
    DocumentIntelligenceLayoutSkill,
    DocumentIntelligenceLayoutSkillChunkingProperties,
    IndexProjectionMode,
    InputFieldMappingEntry,
    OutputFieldMappingEntry,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionSelector,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerKnowledgeStore,
    SearchIndexerKnowledgeStoreObjectProjectionSelector,
    SearchIndexerKnowledgeStoreProjection,
    SearchIndexerSkillset,
    ShaperSkill,
)

from app.models.config_options import AIServicesOptions, AzureOpenAIOptions, BlobStorageOptions, SearchServiceOptions
from app.prompts.templates import IngestionPrompts


class ISkillsetService(ABC):
    """Interface for skillset service operations.

    Provides two implementation strategies:
    - SDK-based: Uses Azure AI Search Python SDK for type-safe model construction
    - REST-based: Uses direct HTTP calls for advanced features and fine-grained control
    """

    @abstractmethod
    async def create_skillset_using_sdk_async(self, skillset_name: str, index_name: str) -> None:
        """
        Create or update a multimodal skillset using the Azure AI Search Python SDK.

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.
        """
        pass

    @abstractmethod
    async def create_skillset_using_rest_async(self, skillset_name: str, index_name: str) -> None:
        """
        Create or update a multimodal skillset using the Azure AI Search REST API.

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.
        """
        pass

    @abstractmethod
    async def create_markdown_skillset_async(self, skillset_name: str, index_name: str) -> None:
        """
        Create or update a skillset for markdown content (split + embedding).

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.
        """
        pass

    @abstractmethod
    async def create_json_skillset_async(self, skillset_name: str, index_name: str) -> None:
        """
        Create or update a skillset for JSON content (split + embedding).

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.
        """
        pass


class SkillsetService(ISkillsetService):
    """
    Service for managing Azure AI Search skillsets.

    Handles creation and configuration of skillsets with:
    - Document Intelligence for layout analysis, text extraction, and image extraction
    - Azure OpenAI for text and image embeddings (3072 dimensions)
    - GPT vision for image verbalization and description
    - Index projections for multimodal content (text and images)
    - Knowledge store for extracted image storage
    """

    # Pre-converted search string literals for Azure AI Search skills
    IMAGE_VERBALIZATION_LITERAL = IngestionPrompts.as_search_string_literal(
        IngestionPrompts.IMAGE_VERBALIZATION_SYSTEM_MESSAGE
    )

    def __init__(
        self,
        search_indexer_client: SearchIndexerClient,
        search_options: SearchServiceOptions,
        openai_options: AzureOpenAIOptions,
        ai_services_options: AIServicesOptions,
        blob_options: BlobStorageOptions,
        logger,
    ) -> None:
        """
        Initialize the SkillsetService.

        Args:
            search_indexer_client: Azure Search indexer client for skillset operations.
            search_options: Configuration for Azure AI Search service.
            openai_options: Configuration for Azure OpenAI service.
            ai_services_options: Configuration for Azure AI Services.
            blob_options: Configuration for blob storage.
        """
        self._search_indexer_client: SearchIndexerClient = search_indexer_client
        self._search_options: SearchServiceOptions = search_options
        self._openai_options: AzureOpenAIOptions = openai_options
        self._ai_services_options: AIServicesOptions = ai_services_options
        self._blob_options: BlobStorageOptions = blob_options
        self.logger = logger

    async def create_skillset_using_sdk_async(self, skillset_name: str, index_name: str) -> None:
        """
        Create or update a multimodal skillset for document processing using Python SDK.

        This method uses the Azure AI Search Python SDK for skillset creation,
        providing type-safe model construction and automatic serialization.

        The skillset includes:
        1. Document Intelligence Layout - Extracts text sections and images with layout metadata
        2. Text Chunk Embedding - Generates vector embeddings for text chunks (3072-dim)
        3. Image Verbalization - Generates text descriptions of images using GPT vision
        4. Image Description Embedding - Generates vector embeddings for image descriptions (3072-dim)
        5. Image Path Shaper - Shapes image paths for knowledge store projection

        Index projections map enriched content to searchable fields.
        Knowledge store (multimodal mode only) saves extracted images to blob storage.

        Authentication:
        - Cognitive Services: Managed identity (AIServicesAccountIdentity)
        - Azure OpenAI: API key or managed identity (based on configuration)
        - Knowledge store: Managed identity via ResourceId connection string

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.
            include_image_processing: Whether to include image verbalization and embeddings. Defaults to True.

        Raises:
            Exception: If skillset creation fails.
        """

        # Define skills using SDK model classes
        skills: list = [
            # Document Intelligence Layout
            DocumentIntelligenceLayoutSkill(
                name="document-intelligence-layout-skill",
                description="Extract text and images with layout from documents using Document Intelligence",
                context="/document",
                output_mode="oneToMany",
                output_format="text",
                markdown_header_depth=None,  # type: ignore[arg-type]
                extraction_options=["images", "locationMetadata"],
                chunking_properties=DocumentIntelligenceLayoutSkillChunkingProperties(
                    unit="characters",
                    maximum_length=3000,
                    overlap_length=500,
                ),
                inputs=[InputFieldMappingEntry(name="file_data", source="/document/file_data")],
                outputs=[
                    OutputFieldMappingEntry(name="text_sections", target_name="text_sections"),
                    OutputFieldMappingEntry(name="normalized_images", target_name="normalized_images"),
                ],
            ),
            # Text Chunk Embedding
            AzureOpenAIEmbeddingSkill(
                name="text-chunk-embedding-skill",
                description="Generate embeddings for text chunks using Azure OpenAI",
                context="/document/text_sections/*",
                resource_url=self._openai_options.resource_uri,
                deployment_name=self._openai_options.text_embedding_model,
                dimensions=3072,
                model_name=self._openai_options.text_embedding_model,
                inputs=[
                    InputFieldMappingEntry(
                        name="text",
                        source="/document/text_sections/*/content",
                    )
                ],
                outputs=[OutputFieldMappingEntry(name="embedding", target_name="text_vector")],
            ),
            # Image Verbalization
            ChatCompletionSkill(
                name="image-verbalization-skill",
                description="Generate text descriptions of images using GPT vision",
                context="/document/normalized_images/*",
                uri=self._openai_options.effective_image_uri,
                timeout=timedelta(seconds=230),
                inputs=[
                    InputFieldMappingEntry(
                        name="systemMessage",
                        source=self.IMAGE_VERBALIZATION_LITERAL,
                    ),
                    InputFieldMappingEntry(
                        name="userMessage",
                        source="='Please describe this image.'",
                    ),
                    InputFieldMappingEntry(
                        name="image",
                        source="/document/normalized_images/*/data",
                    ),
                ],
                outputs=[OutputFieldMappingEntry(name="response", target_name="verbalizedImage")],
            ),
            # Image Description Embedding
            AzureOpenAIEmbeddingSkill(
                name="image-description-embedding-skill",
                description="Generate embeddings for image descriptions using Azure OpenAI",
                context="/document/normalized_images/*",
                resource_url=self._openai_options.resource_uri,
                deployment_name=self._openai_options.text_embedding_model,
                dimensions=3072,
                model_name=self._openai_options.text_embedding_model,
                inputs=[
                    InputFieldMappingEntry(
                        name="text",
                        source="/document/normalized_images/*/verbalizedImage",
                    )
                ],
                outputs=[
                    OutputFieldMappingEntry(
                        name="embedding",
                        target_name="verbalizedImage_vector",
                    )
                ],
            ),
            # Image Path Shaper
            ShaperSkill(
                name="image-path-shaper-skill",
                context="/document/normalized_images/*",
                inputs=[
                    InputFieldMappingEntry(
                        name="normalized_images",
                        source="/document/normalized_images/*",
                    ),
                    InputFieldMappingEntry(
                        name="imagePath",
                        source=f"='{self._blob_options.images_container_name}/'+$(/document/normalized_images/*/imagePath)",
                    ),
                ],
                outputs=[OutputFieldMappingEntry(name="output", target_name="new_normalized_images")],
            ),
        ]

        # Index projections
        projection_selectors = [
            SearchIndexerIndexProjectionSelector(
                target_index_name=index_name,
                parent_key_field_name="text_document_id",
                source_context="/document/text_sections/*",
                mappings=[
                    InputFieldMappingEntry(
                        name="content_embedding",
                        source="/document/text_sections/*/text_vector",
                    ),
                    InputFieldMappingEntry(
                        name="content_text",
                        source="/document/text_sections/*/content",
                    ),
                    InputFieldMappingEntry(
                        name="location_metadata",
                        source="/document/text_sections/*/locationMetadata",
                    ),
                    InputFieldMappingEntry(
                        name="document_title",
                        source="/document/document_title",
                    ),
                    InputFieldMappingEntry(
                        name="sp_site_name",
                        source="/document/sp_site_name",
                    ),
                    InputFieldMappingEntry(
                        name="sp_library_name",
                        source="/document/sp_library_name",
                    ),
                    InputFieldMappingEntry(
                        name="sp_last_modified_utc",
                        source="/document/sp_last_modified_utc",
                    ),
                    InputFieldMappingEntry(
                        name="sp_filename",
                        source="/document/sp_filename",
                    ),
                    InputFieldMappingEntry(
                        name="sp_file_path",
                        source="/document/sp_file_path",
                    ),
                    InputFieldMappingEntry(
                        name="sp_file_size_bytes",
                        source="/document/sp_file_size_bytes",
                    ),
                ],
            ),
            SearchIndexerIndexProjectionSelector(
                target_index_name=index_name,
                parent_key_field_name="image_document_id",
                source_context="/document/normalized_images/*",
                mappings=[
                    InputFieldMappingEntry(
                        name="content_text",
                        source="/document/normalized_images/*/verbalizedImage",
                    ),
                    InputFieldMappingEntry(
                        name="content_embedding",
                        source="/document/normalized_images/*/verbalizedImage_vector",
                    ),
                    InputFieldMappingEntry(
                        name="content_path",
                        source="/document/normalized_images/*/new_normalized_images/imagePath",
                    ),
                    InputFieldMappingEntry(
                        name="document_title",
                        source="/document/document_title",
                    ),
                    InputFieldMappingEntry(
                        name="location_metadata",
                        source="/document/normalized_images/*/locationMetadata",
                    ),
                    InputFieldMappingEntry(
                        name="sp_site_name",
                        source="/document/sp_site_name",
                    ),
                    InputFieldMappingEntry(
                        name="sp_library_name",
                        source="/document/sp_library_name",
                    ),
                    InputFieldMappingEntry(
                        name="sp_last_modified_utc",
                        source="/document/sp_last_modified_utc",
                    ),
                    InputFieldMappingEntry(
                        name="sp_filename",
                        source="/document/sp_filename",
                    ),
                    InputFieldMappingEntry(
                        name="sp_file_path",
                        source="/document/sp_file_path",
                    ),
                    InputFieldMappingEntry(
                        name="sp_file_size_bytes",
                        source="/document/sp_file_size_bytes",
                    ),
                ],
            ),
        ]

        index_projections = SearchIndexerIndexProjection(
            selectors=projection_selectors,
            parameters=SearchIndexerIndexProjectionsParameters(
                projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
            ),
        )

        # Define knowledge store for extracted images
        knowledge_store = SearchIndexerKnowledgeStore(
            storage_connection_string=f"ResourceId={self._blob_options.resource_id}",
            projections=[
                SearchIndexerKnowledgeStoreProjection(
                    objects=[
                        SearchIndexerKnowledgeStoreObjectProjectionSelector(
                            storage_container=self._blob_options.images_container_name,
                            source="/document/normalized_images/*",
                        )
                    ]
                )
            ],
            parameters={"synthesizeGeneratedKeyName": True},  # type: ignore[arg-type]
        )

        # Use the full endpoint URL for managed identity
        endpoint_str = str(self._ai_services_options.cognitive_services_endpoint).rstrip("/")
        self.logger.info(f"Using managed identity authentication for Cognitive Services at {endpoint_str}")
        cognitive_services_account = AIServicesAccountIdentity(subdomain_url=endpoint_str)

        skillset = SearchIndexerSkillset(
            name=skillset_name,
            description="A skillset for multimodal document processing with text and image extraction",
            skills=skills,
            cognitive_services_account=cognitive_services_account,
            index_projection=index_projections,
            knowledge_store=knowledge_store,
        )

        # Create or update skillset using SDK
        await self._search_indexer_client.create_or_update_skillset(skillset)
        self.logger.info(f"Skillset '{skillset_name}' created or updated successfully using SDK (multimodal mode).")

    async def create_skillset_using_rest_async(
        self,
        skillset_name: str,
        index_name: str,
    ) -> None:
        """
        Create or update a multimodal skillset for document processing using REST API.

        This method uses the Azure AI Search REST API directly (via httpx) for skillset creation,
        providing more control over advanced features and parameters not fully exposed in the Python SDK.

        The skillset includes:
        1. Document Intelligence Layout - Extracts text sections and images with layout metadata
        2. Text Chunk Embedding - Generates vector embeddings for text chunks (3072-dim)
        3. Image Verbalization - Generates text descriptions of images using GPT vision
        4. Image Description Embedding - Generates vector embeddings for image descriptions (3072-dim)
        5. Image Path Shaper - Shapes image paths for knowledge store projection

        Index projections map enriched content to searchable fields.
        Knowledge store (multimodal mode only) saves extracted images to blob storage.

        Authentication:
        - Search service: Admin API key (required)
        - Cognitive Services: Managed identity (AIServicesByIdentity)
        - Azure OpenAI: API key or managed identity (based on configuration)
        - Knowledge store: Managed identity via ResourceId connection string

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.

        Raises:
            Exception: If skillset creation fails (HTTP error response from Search service).
        """

        endpoint_str = str(self._ai_services_options.cognitive_services_endpoint).rstrip("/")
        cognitive_services = {
            "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity",
            "subdomainUrl": endpoint_str,
        }

        # Skills
        skills: list[dict[str, Any]] = [
            {
                "@odata.type": "#Microsoft.Skills.Util.DocumentIntelligenceLayoutSkill",
                "name": "document-intelligence-layout-skill",
                "description": "Extract text and images with layout from documents using Document Intelligence",
                "context": "/document",
                "outputMode": "oneToMany",
                "outputFormat": "text",
                "extractionOptions": ["images", "locationMetadata"],
                "chunkingProperties": {"unit": "characters", "maximumLength": 3000, "overlapLength": 500},
                "inputs": [{"name": "file_data", "source": "/document/file_data"}],
                "outputs": [
                    {"name": "text_sections", "targetName": "text_sections"},
                    {"name": "normalized_images", "targetName": "normalized_images"},
                ],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                "name": "text-chunk-embedding-skill",
                "description": "Generate embeddings for text chunks using Azure OpenAI",
                "context": "/document/text_sections/*",
                "resourceUri": self._openai_options.resource_uri,
                "deploymentId": self._openai_options.text_embedding_model,
                "dimensions": 3072,
                "modelName": self._openai_options.text_embedding_model,
                "inputs": [{"name": "text", "source": "/document/text_sections/*/content"}],
                "outputs": [{"name": "embedding", "targetName": "text_vector"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Custom.ChatCompletionSkill",
                "name": "image-verbalization-skill",
                "description": "Generate text descriptions of images using GPT vision",
                "context": "/document/normalized_images/*",
                "uri": self._openai_options.effective_image_uri,
                "timeout": "PT230S",
                "inputs": [
                    {"name": "systemMessage", "source": self.IMAGE_VERBALIZATION_LITERAL},
                    {"name": "userMessage", "source": "='Please describe this image.'"},
                    {"name": "image", "source": "/document/normalized_images/*/data"},
                ],
                "outputs": [{"name": "response", "targetName": "verbalizedImage"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                "name": "image-description-embedding-skill",
                "description": "Generate embeddings for image descriptions using Azure OpenAI",
                "context": "/document/normalized_images/*",
                "resourceUri": self._openai_options.resource_uri,
                "deploymentId": self._openai_options.text_embedding_model,
                "dimensions": 3072,
                "modelName": self._openai_options.text_embedding_model,
                "inputs": [{"name": "text", "source": "/document/normalized_images/*/verbalizedImage"}],
                "outputs": [{"name": "embedding", "targetName": "verbalizedImage_vector"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Util.ShaperSkill",
                "name": "image-path-shaper-skill",
                "context": "/document/normalized_images/*",
                "inputs": [
                    {"name": "normalized_images", "source": "/document/normalized_images/*"},
                    {
                        "name": "imagePath",
                        "source": f"='{self._blob_options.images_container_name}/'+$(/document/normalized_images/*/imagePath)",
                    },
                ],
                "outputs": [{"name": "output", "targetName": "new_normalized_images"}],
            },
        ]

        # Index projections
        selectors: list[dict[str, Any]] = [
            {
                "targetIndexName": index_name,
                "parentKeyFieldName": "text_document_id",
                "sourceContext": "/document/text_sections/*",
                "mappings": [
                    {"name": "content_embedding", "source": "/document/text_sections/*/text_vector"},
                    {"name": "content_text", "source": "/document/text_sections/*/content"},
                    {"name": "location_metadata", "source": "/document/text_sections/*/locationMetadata"},
                    {"name": "document_title", "source": "/document/document_title"},
                    {"name": "sp_site_name", "source": "/document/sp_site_name"},
                    {"name": "sp_library_name", "source": "/document/sp_library_name"},
                    {"name": "sp_last_modified_utc", "source": "/document/sp_last_modified_utc"},
                    {"name": "sp_filename", "source": "/document/sp_filename"},
                    {"name": "sp_file_path", "source": "/document/sp_file_path"},
                    {"name": "sp_file_size_bytes", "source": "/document/sp_file_size_bytes"},
                ],
            },
            {
                "targetIndexName": index_name,
                "parentKeyFieldName": "image_document_id",
                "sourceContext": "/document/normalized_images/*",
                "mappings": [
                    {"name": "content_text", "source": "/document/normalized_images/*/verbalizedImage"},
                    {"name": "content_embedding", "source": "/document/normalized_images/*/verbalizedImage_vector"},
                    {"name": "content_path", "source": "/document/normalized_images/*/new_normalized_images/imagePath"},
                    {"name": "document_title", "source": "/document/document_title"},
                    {"name": "location_metadata", "source": "/document/normalized_images/*/locationMetadata"},
                    {"name": "sp_site_name", "source": "/document/sp_site_name"},
                    {"name": "sp_library_name", "source": "/document/sp_library_name"},
                    {"name": "sp_last_modified_utc", "source": "/document/sp_last_modified_utc"},
                    {"name": "sp_filename", "source": "/document/sp_filename"},
                    {"name": "sp_file_path", "source": "/document/sp_file_path"},
                    {"name": "sp_file_size_bytes", "source": "/document/sp_file_size_bytes"},
                ],
            },
        ]

        index_projections: dict[str, Any] = {
            "selectors": selectors,
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        }

        payload: dict[str, Any] = {
            "name": skillset_name,
            "description": "A skillset for multimodal document processing with text and image extraction",
            "cognitiveServices": cognitive_services,
            "skills": skills,
            "indexProjections": index_projections,
        }

        # Knowledge store for extracted images
        storage_arm_id = self._blob_options.resource_id
        if isinstance(storage_arm_id, str) and storage_arm_id.startswith("ResourceId="):
            storage_arm_id = storage_arm_id.split("ResourceId=", 1)[1]

        payload["knowledgeStore"] = {
            "storageConnectionString": f"ResourceId={storage_arm_id}",
            "projections": [
                {
                    "objects": [
                        {
                            "storageContainer": self._blob_options.images_container_name,
                            "source": "/document/normalized_images/*",
                        }
                    ]
                }
            ],
            "parameters": {"synthesizeGeneratedKeyName": True},
        }

        # REST PUT
        endpoint = str(self._search_options.endpoint).rstrip("/")
        url = f"{endpoint}/skillsets/{skillset_name}"
        params = {"api-version": self._search_options.skillset_api_version}

        headers = {
            "api-key": self._search_options.api_key,  # admin key
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            resp = await client.put(url, params=params, headers=headers, json=payload)

        if 200 <= resp.status_code < 300:
            self.logger.info(f"Skillset '{skillset_name}' created or updated successfully via REST (multimodal mode).")
            return

        raise Exception(f"Error creating skillset '{skillset_name}' via REST: {resp.status_code} - {resp.text}")

    async def _create_text_embedding_skillset_async(
        self, skillset_name: str, index_name: str, description: str
    ) -> None:
        """Create a skillset with SplitSkill + AzureOpenAIEmbeddingSkill via REST API.

        Chunks the extracted text with a SplitSkill (3000 chars, 500 overlap)
        then generates embeddings for each chunk.  Index projections map each
        chunk to a separate search document in the target index.

        Uses the REST API directly (same approach as the multimodal skillset)
        to avoid opaque 404 errors from the Python SDK.

        Used by both the markdown and JSON pipelines.

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.
            description: A human-readable description of the skillset.
        """
        endpoint_str = str(self._ai_services_options.cognitive_services_endpoint).rstrip("/")
        cognitive_services = {
            "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity",
            "subdomainUrl": endpoint_str,
        }

        skills: list[dict[str, Any]] = [
            {
                "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                "name": "text-split-skill",
                "description": "Split text into chunks for embedding",
                "context": "/document",
                "textSplitMode": "pages",
                "maximumPageLength": 3000,
                "pageOverlapLength": 500,
                "unit": "characters",
                "inputs": [{"name": "text", "source": "/document/content"}],
                "outputs": [{"name": "textItems", "targetName": "chunks"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                "name": "text-embedding-skill",
                "description": "Generate embeddings for text chunks using Azure OpenAI",
                "context": "/document/chunks/*",
                "resourceUri": self._openai_options.resource_uri,
                "deploymentId": self._openai_options.text_embedding_model,
                "dimensions": 3072,
                "modelName": self._openai_options.text_embedding_model,
                "inputs": [{"name": "text", "source": "/document/chunks/*"}],
                "outputs": [{"name": "embedding", "targetName": "text_vector"}],
            },
        ]

        index_projections: dict[str, Any] = {
            "selectors": [
                {
                    "targetIndexName": index_name,
                    "parentKeyFieldName": "text_document_id",
                    "sourceContext": "/document/chunks/*",
                    "mappings": [
                        {"name": "content_embedding", "source": "/document/chunks/*/text_vector"},
                        {"name": "content_text", "source": "/document/chunks/*"},
                        {"name": "document_title", "source": "/document/document_title"},
                        {"name": "sp_site_name", "source": "/document/sp_site_name"},
                        {"name": "sp_library_name", "source": "/document/sp_library_name"},
                        {"name": "sp_last_modified_utc", "source": "/document/sp_last_modified_utc"},
                        {"name": "sp_filename", "source": "/document/sp_filename"},
                        {"name": "sp_file_path", "source": "/document/sp_file_path"},
                        {"name": "sp_file_size_bytes", "source": "/document/sp_file_size_bytes"},
                    ],
                },
            ],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        }

        payload: dict[str, Any] = {
            "name": skillset_name,
            "description": description,
            "cognitiveServices": cognitive_services,
            "skills": skills,
            "indexProjections": index_projections,
        }

        endpoint = str(self._search_options.endpoint).rstrip("/")
        url = f"{endpoint}/skillsets/{skillset_name}"
        params = {"api-version": self._search_options.skillset_api_version}

        headers = {
            "api-key": self._search_options.api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            resp = await client.put(url, params=params, headers=headers, json=payload)

        if 200 <= resp.status_code < 300:
            self.logger.info(f"Skillset '{skillset_name}' created or updated successfully via REST.")
            return

        raise Exception(f"Error creating skillset '{skillset_name}' via REST: {resp.status_code} - {resp.text}")

    async def create_markdown_skillset_async(self, skillset_name: str, index_name: str) -> None:
        """Create or update a skillset for markdown content.

        Splits text with a SplitSkill then embeds each chunk. Index
        projections map chunks to individual search documents.

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.
        """
        await self._create_text_embedding_skillset_async(
            skillset_name,
            index_name,
            "Skillset for markdown content \u2014 chunks and embeds text for vector search",
        )

    async def create_json_skillset_async(self, skillset_name: str, index_name: str) -> None:
        """Create or update a skillset for JSON content.

        Splits text with a SplitSkill then embeds each chunk. Index
        projections map chunks to individual search documents.

        Args:
            skillset_name: The name of the skillset to create.
            index_name: The name of the target index for projections.
        """
        await self._create_text_embedding_skillset_async(
            skillset_name,
            index_name,
            "Skillset for JSON content \u2014 chunks and embeds text for vector search",
        )

