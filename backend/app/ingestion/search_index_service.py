"""Search index service for Azure AI Search.

Manages the creation and configuration of search indexes, including
vector search (HNSW + scalar quantization), Azure OpenAI vectorizer
integration, and semantic search configuration.
"""
from abc import ABC, abstractmethod

from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    ComplexField,
    HnswAlgorithmConfiguration,
    HnswParameters,
    LexicalAnalyzerName,
    ScalarQuantizationCompression,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchAlgorithmMetric,
    VectorSearchProfile,
)

from app.models.config_options import AzureOpenAIOptions


class ISearchIndexService(ABC):
    """Abstract interface for Azure AI Search index service operations."""

    @abstractmethod
    async def create_search_index_async(
        self, index_name: str, include_image_processing: bool = True
    ) -> None:
        """Create or update a search index with vector and semantic search capabilities.

        Args:
            index_name: The name of the index to create.
            include_image_processing: Whether to optimise for multimodal (text + images)
                or text-only content. Default is ``True`` (multimodal).
        """
        pass


class SearchIndexService(ISearchIndexService):
    """Service for managing Azure AI Search indexes.

    Handles creation and configuration of search indexes with:
    - Vector search with HNSW algorithm
    - Scalar quantization compression
    - Azure OpenAI vectorizer integration
    - Semantic search configuration
    """

    # Dimensions produced by text-embedding-3-large
    _CONTENT_EMBEDDING_DIMENSIONS: int = 3072

    def __init__(
        self,
        index_client: SearchIndexClient,
        openai_options: AzureOpenAIOptions,
        logger,
    ) -> None:
        """Initialize the SearchIndexService.

        Args:
            index_client: Azure Search index client for managing indexes.
            openai_options: Configuration options for Azure OpenAI.
            logger: Injected logging service.
        """
        self._index_client: SearchIndexClient = index_client
        self._openai_options: AzureOpenAIOptions = openai_options
        self.logger = logger

    async def create_search_index_async(
        self, index_name: str, include_image_processing: bool = True
    ) -> None:
        """Create or update a search index with vector and semantic search capabilities.

        The index is configured with:
        - Text and vector fields for multimodal content
        - HNSW vector search algorithm with cosine metric (adaptive parameters)
        - Scalar quantization for efficient storage
        - Azure OpenAI vectorizer for embedding generation
        - Semantic search for improved relevance

        Args:
            index_name: The name of the index to create.
            include_image_processing: Whether to optimise for multimodal (text + images)
                or text-only content. Affects HNSW parameters (m, ef_construction).
                Default is ``True`` (multimodal with m=12, ef_construction=500).

        Raises:
            Exception: If index creation fails.
        """
        fields = self._create_index_fields()
        vector_search = self._create_vector_search_config(include_image_processing)
        semantic_search = self._create_semantic_search_config()

        index = SearchIndex(
            name=index_name,
            fields=fields,
            vector_search=vector_search,
            semantic_search=semantic_search,
        )

        await self._index_client.create_or_update_index(index)
        self.logger.info(f"Index '{index_name}' created or updated successfully.")

    def _create_index_fields(self) -> list[SearchField]:
        """
        Create the field schema for the search index.

        Returns:
            List of SearchField definitions for the index.
        """
        return [
            SearchField(
                name="content_id",
                type=SearchFieldDataType.String,
                key=True,
                filterable=True,
                sortable=True,
                facetable=False,
                analyzer_name=LexicalAnalyzerName.KEYWORD,
            ),
            SearchField(
                name="text_document_id",
                type=SearchFieldDataType.String,
                searchable=False,
                filterable=True,
                sortable=False,
                facetable=False,
            ),
            SearchField(
                name="image_document_id",
                type=SearchFieldDataType.String,
                searchable=False,
                filterable=True,
                sortable=False,
                facetable=False,
            ),
            SearchField(
                name="document_title",
                type=SearchFieldDataType.String,
                searchable=True,
                filterable=True,
                sortable=False,
                facetable=False,
            ),
            SearchField(
                name="content_text",
                type=SearchFieldDataType.String,
                searchable=True,
                filterable=False,
                sortable=False,
                facetable=False,
            ),
            SearchField(
                name="content_embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                retrievable=True,
                vector_search_dimensions=self._CONTENT_EMBEDDING_DIMENSIONS,
                vector_search_profile_name="hnsw",
            ),
            SearchField(
                name="content_path",
                type=SearchFieldDataType.String,
                searchable=False,
                filterable=False,
                sortable=False,
                facetable=False,
            ),
            ComplexField(
                name="location_metadata",
                fields=[
                    SimpleField(
                        name="pageNumber",
                        type=SearchFieldDataType.Int32,
                        filterable=True,
                    )
                ],
            ),
        ]

    def _create_vector_search_config(
        self, include_image_processing: bool = True
    ) -> VectorSearch:
        """Create vector search configuration with HNSW algorithm and compression.

        Args:
            include_image_processing: If ``True``, uses multimodal-optimised parameters
                (m=12, ef_construction=500) for better recall with diverse content.
                If ``False``, uses text-only parameters (m=8, ef_construction=400) for
                efficiency with homogeneous content.

        Returns:
            ``VectorSearch`` configuration object.
        """
        # Adaptive HNSW parameters based on content type
        if include_image_processing:
            # Multimodal: Higher connectivity for diverse content (text + images)
            m_value = 12
            ef_construction_value = 500
        else:
            # Text-only: Balanced settings for homogeneous content
            m_value = 8
            ef_construction_value = 400

        hnsw_config = HnswAlgorithmConfiguration(
            name="defaulthnsw",
            parameters=HnswParameters(
                m=m_value,
                ef_construction=ef_construction_value,
                metric=VectorSearchAlgorithmMetric.COSINE,
            ),
        )

        # Scalar quantization compression
        scalar_compression = ScalarQuantizationCompression(
            compression_name="scalar-quant-8bit"
        )

        # Azure OpenAI vectorizer
        vectorizer = AzureOpenAIVectorizer(
            vectorizer_name="multi-modal-vectorizer",
            parameters=AzureOpenAIVectorizerParameters(
                resource_url=self._openai_options.resource_uri,
                deployment_name=self._openai_options.text_embedding_model,
                model_name=self._openai_options.text_embedding_model,
            ),
        )

        # Vector search profile
        vector_profile = VectorSearchProfile(
            name="hnsw",
            algorithm_configuration_name="defaulthnsw",
            vectorizer_name="multi-modal-vectorizer",
            compression_name="scalar-quant-8bit",
        )

        # Initialize VectorSearch with all components
        vector_search = VectorSearch(
            algorithms=[hnsw_config],
            vectorizers=[vectorizer],
            profiles=[vector_profile],
            compressions=[scalar_compression],
        )

        return vector_search

    def _create_semantic_search_config(self) -> SemanticSearch:
        """Create semantic search configuration.

        Configures semantic ranking with:
        - Title field: document_title (highest priority)
        - Content fields: content_text (main searchable content)
        Returns:
            ``SemanticSearch`` configuration object.
        """
        semantic_config = SemanticConfiguration(
            name="semanticconfig",
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name="document_title"),
                content_fields=[
                    SemanticField(field_name="content_text")
                ],
                keywords_fields=[],
            ),
        )

        return SemanticSearch(
            default_configuration_name="semanticconfig",
            configurations=[semantic_config],
        )
