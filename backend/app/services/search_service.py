"""Azure AI Search service with hybrid search, semantic ranking, and filtering.

This module provides comprehensive search capabilities combining vector similarity
and keyword search with semantic ranking for RAG applications.

Features:
    - Hybrid search (keyword + vector with RRF)
    - Semantic ranking
    - Reranker score filtering (drops low-relevance results below a configurable threshold)
    - Metadata filtering
    - Result deduplication
    - Exponential backoff retry
    - Multiple search modes (vector, keyword, hybrid)

Usage:
    search_service = SearchService(search_client, openai_options)
    results = await search_service.search_async(
        query="What are Azure best practices?",
        top_k=5,
        search_mode="hybrid",
        use_semantic_ranking=True
    )
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from azure.core.exceptions import HttpResponseError
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryAnswerType, QueryCaptionType, QueryType, VectorizedQuery
from openai import AsyncAzureOpenAI

from app.core.logger import Logger
from app.models import AzureOpenAIOptions
from app.models.chat import RetrievedDocument

# Azure OpenAI API version used by both the embedding and chat clients.
OPENAI_API_VERSION = "2024-06-01"


class ISearchService(ABC):
    """Interface for search service operations."""

    @abstractmethod
    async def search_async(
        self,
        query: str,
        top_k: int = 5,
        search_mode: str = "hybrid",
        filters: dict[str, Any] | None = None,
        use_semantic_ranking: bool = True,
        deduplicate: bool = True,
        exclude_ids: list[str] | None = None,
    ) -> list[RetrievedDocument]:
        """Execute a search against the Azure AI Search index.

        Args:
            query: The search query text
            top_k: Number of results to return
            search_mode: 'vector', 'keyword', or 'hybrid'
            filters: Optional metadata filters
            use_semantic_ranking: Whether to use semantic ranking
            deduplicate: Whether to deduplicate results
            exclude_ids: Content IDs to exclude from results (for iterative search)

        Returns:
            List of RetrievedDocument objects ordered by relevance
        """
        pass

    @abstractmethod
    async def generate_embedding_async(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text.

        Args:
            text: The text to embed

        Returns:
            List of floats representing the embedding vector
        """
        pass

    @abstractmethod
    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple text strings.

        Args:
            texts: List of input texts to embed

        Returns:
            List of embedding vectors
        """
        pass

    @abstractmethod
    def get_embedding_dimensions(self) -> int:
        """Get the dimensionality of generated embeddings.

        Returns:
            Embedding dimension count
        """
        pass


class SearchService(ISearchService):
    """Service for executing hybrid searches against Azure AI Search.

    Combines vector search (using embeddings) with full-text search
    and applies semantic ranking for optimal RAG retrieval.

    Features:
        - Hybrid search (vector + keyword)
        - Semantic ranking for improved relevance
        - Reranker score filtering: drops results below ``min_reranker_score`` when
          semantic ranking is enabled (configurable via ``SEARCHSERVICE_MIN_RERANKER_SCORE``)
        - Metadata filtering (date range, document type, category, custom)
        - Result deduplication
        - Exponential backoff retry
        - Exclusion filters for iterative search
    """

    def __init__(
        self,
        search_client: SearchClient,
        openai_options: AzureOpenAIOptions,
        logger: Logger,
        vector_field_name: str = "content_embedding",
        index_name: str | None = None,
        min_reranker_score: float = 2.0,
    ) -> None:
        """Initialize the SearchService.

        Args:
            search_client: Azure Search client for executing queries
            openai_options: Configuration for Azure OpenAI embeddings
            logger: Injected logging service
            vector_field_name: Name of the vector field in the index
            index_name: Optional index name for logging
            min_reranker_score: Minimum reranker score to retain a result when
                semantic ranking is enabled (Azure AI Search scores range 0-4)
        """
        self._search_client: SearchClient = search_client
        self._openai_options: AzureOpenAIOptions = openai_options
        self.logger: Logger = logger
        self._openai_client: AsyncAzureOpenAI | None = None
        self._vector_field_name: str = vector_field_name
        self._index_name: str = index_name or "search-index"
        self._min_reranker_score: float = min_reranker_score

        self.logger.info(f"SearchService initialized for index: {self._index_name}")

    # Embedding dimension lookup by model name. Defined as a class constant so
    # it is created once rather than rebuilt on every get_embedding_dimensions() call.
    _EMBEDDING_DIMENSIONS: dict[str, int] = {
        "text-embedding-ada-002": 1536,
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    async def _get_openai_client(self) -> AsyncAzureOpenAI:
        """Get or create the Azure OpenAI client (lazy initialization)."""
        if self._openai_client is None:
            if self._openai_options.api_key:
                self._openai_client = AsyncAzureOpenAI(
                    azure_endpoint=self._openai_options.resource_uri,
                    api_key=self._openai_options.api_key,
                    api_version=OPENAI_API_VERSION,
                )
            else:
                # Use managed identity
                credential = DefaultAzureCredential()
                token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
                self._openai_client = AsyncAzureOpenAI(
                    azure_endpoint=self._openai_options.resource_uri,
                    azure_ad_token_provider=token_provider,
                    api_version=OPENAI_API_VERSION,
                )
        return self._openai_client

    async def generate_embedding_async(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text using Azure OpenAI.

        Args:
            text: The text to embed

        Returns:
            List of floats representing the embedding vector

        Raises:
            Exception: If embedding generation fails
        """
        client = await self._get_openai_client()

        response = await client.embeddings.create(
            input=text,
            model=self._openai_options.text_embedding_model,
        )

        return response.data[0].embedding

    async def search_async(
        self,
        query: str,
        query_vector: list[float] | None = None,
        top_k: int = 5,
        search_mode: str = "hybrid",
        filters: dict[str, Any] | None = None,
        use_semantic_ranking: bool = True,
        deduplicate: bool = True,
        exclude_ids: list[str] | None = None,
    ) -> list[RetrievedDocument]:
        """Execute search with vector and/or keyword components.

        Args:
            query: Search query text
            query_vector: Optional pre-computed query embedding
            top_k: Number of results to return
            search_mode: 'vector', 'keyword', or 'hybrid'
            filters: Optional metadata filters (date_from, date_to, document_type, category, custom)
            use_semantic_ranking: Whether to apply semantic ranking. When True, results
                with a reranker score below ``min_reranker_score`` are also filtered out.
            deduplicate: Whether to deduplicate results
            exclude_ids: Content IDs to exclude from results (for iterative search)

        Returns:
            List of retrieved documents with metadata, ordered by relevance
        """
        try:
            # Generate query embedding if not provided and needed
            if search_mode in ["vector", "hybrid"] and query_vector is None:
                query_vector = await self.generate_embedding_async(query)

            # Build filter expression
            filter_expr = self._build_filter_expression(filters, exclude_ids)

            # Prepare vector query if needed
            vector_query = None
            if search_mode in ["vector", "hybrid"] and query_vector:
                vector_query = VectorizedQuery(
                    vector=query_vector,
                    k_nearest_neighbors=top_k * 2 if search_mode == "hybrid" else top_k,
                    fields=self._vector_field_name,
                )

            # Determine search text based on mode
            search_text = query if search_mode in ["keyword", "hybrid"] else ""

            # Execute search with retry
            results = await self._search_with_retry(
                search_text=search_text,
                vector_queries=[vector_query] if vector_query else [],
                filter=filter_expr,
                top=top_k,
                use_semantic_ranking=use_semantic_ranking,
            )

            # Parse results
            documents = self._parse_results(results)

            # When semantic ranking is active, drop results below the minimum reranker score
            if use_semantic_ranking:
                documents = self._filter_by_reranker_score(documents)

            # Deduplicate if requested
            if deduplicate:
                documents = self._deduplicate_results(documents)

            self.logger.info(f"Search ({search_mode}) returned {len(documents)} documents for query: {query[:50]}...")

            return documents

        except Exception as e:
            self.logger.error(f"Search failed for query '{query}': {e}", exc_info=True)
            raise

    async def _search_with_retry(
        self,
        search_text: str,
        vector_queries: list[VectorizedQuery],
        filter: str | None,
        top: int,
        use_semantic_ranking: bool,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> list[dict[str, Any]]:
        """Execute search with exponential backoff retry.

        Args:
            search_text: Keyword search query.
            vector_queries: Vector search queries.
            filter: OData filter expression.
            top: Number of results to return.
            use_semantic_ranking: Whether to enable semantic ranking (``QueryType.SEMANTIC``).
            max_retries: Maximum number of retry attempts.
            base_delay: Base delay in seconds for exponential backoff.

        Returns:
            Raw search results as a list of result dicts from Azure AI Search.
        """
        # Build search parameters once — reused across every retry attempt.
        search_params: dict[str, Any] = {
            "search_text": search_text,
            "vector_queries": vector_queries,
            "filter": filter,
            "top": top,
            "select": [
                "content_id",
                "text_document_id",
                "image_document_id",
                "document_title",
                "content_text",
                "content_path",
                "location_metadata",
            ],
        }

        if use_semantic_ranking:
            search_params.update(
                {
                    "query_type": QueryType.SEMANTIC,
                    "semantic_configuration_name": "semanticconfig",  # TODO: move to settings
                    "query_caption": QueryCaptionType.EXTRACTIVE,
                    "query_answer": QueryAnswerType.EXTRACTIVE,
                }
            )

        for attempt in range(max_retries + 1):
            try:
                response = await self._search_client.search(**search_params)

                results = []
                async for result in response:
                    results.append(result)

                return results

            except HttpResponseError as e:
                if e.status_code == 429 and attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    self.logger.warning(
                        f"Search rate limited, retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

            except Exception as e:
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    self.logger.warning(
                        f"Search failed, retrying in {delay}s (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

        raise RuntimeError(f"Search failed after {max_retries} retries")

    def _filter_by_reranker_score(self, documents: list[RetrievedDocument]) -> list[RetrievedDocument]:
        """Drop documents whose reranker score is below the minimum threshold.

        Only documents that actually carry a reranker score are filtered; documents
        without one (e.g. when semantic ranking was unexpectedly absent) are kept.

        Args:
            documents: Parsed search results.

        Returns:
            Filtered list with low-relevance results removed.
        """
        filtered = [
            doc for doc in documents if doc.reranker_score is None or doc.reranker_score >= self._min_reranker_score
        ]
        dropped = len(documents) - len(filtered)
        if dropped:
            self.logger.info(
                f"[RerankerFilter] Dropped {dropped}/{len(documents)} results "
                f"with reranker_score < {self._min_reranker_score}"
            )
        return filtered

    def _build_filter_expression(
        self, filters: dict[str, Any] | None, exclude_ids: list[str] | None = None
    ) -> str | None:
        """Build OData filter expression from filter dictionary.

        The configured search index does not include a ``metadata`` complex
        field, so ``date_from``/``date_to``/``document_type``/``category``
        values are ignored with a warning. Supported inputs:

        - custom: Raw OData expression appended as-is.
        - exclude_ids: Content IDs to exclude via ``search.in``.

        Args:
            filters: Dictionary of filter criteria
            exclude_ids: Content IDs to exclude from results

        Returns:
            OData filter expression or None
        """
        filter_parts: list[str] = []

        if filters:
            unsupported = [
                key
                for key in ("date_from", "date_to", "document_type", "category")
                if filters.get(key)
            ]
            if unsupported:
                self.logger.warning(
                    f"[SearchService] Ignoring unsupported filter(s) {unsupported}; "
                    "index has no 'metadata' field."
                )

            # Custom OData expression (caller-provided, must match index schema)
            custom = filters.get("custom")
            if custom:
                filter_parts.append(custom)

        # Add exclusion filter for already processed documents
        if exclude_ids:
            # Escape single quotes in IDs and join with commas
            escaped_ids = [id.replace("'", "''") for id in exclude_ids]
            excluded_ids_str = ",".join(escaped_ids)
            filter_parts.append(f"not search.in(content_id, '{excluded_ids_str}', ',')")

        return " and ".join(filter_parts) if filter_parts else None

    def _parse_results(self, results: list[dict[str, Any]]) -> list[RetrievedDocument]:
        """Parse raw Azure AI Search results into ``RetrievedDocument`` objects.

        Args:
            results: Raw result dicts returned by the Azure AI Search client.

        Returns:
            List of parsed documents; malformed results are skipped with a warning.
        """
        documents = []

        for result in results:
            try:
                # Prefer text_document_id; fall back to image_document_id.
                document_id = result.get("text_document_id") or result.get("image_document_id") or ""

                doc = RetrievedDocument(
                    document_id=document_id,
                    content_id=result.get("content_id") or "",
                    title=result.get("document_title") or "",
                    content=result.get("content_text") or "",
                    source=result.get("content_path") or "",
                    page_number=result.get("location_metadata", {}).get("pageNumber"),
                    score=result.get("@search.score", 0.0),
                    reranker_score=result.get("@search.reranker_score"),
                    metadata={},
                )
                documents.append(doc)

            except Exception as e:
                self.logger.warning(f"Failed to parse search result: {e}", exc_info=True)
                continue

        return documents

    def _deduplicate_results(
        self,
        documents: list[RetrievedDocument],
        similarity_threshold: float = 0.95,
    ) -> list[RetrievedDocument]:
        """Deduplicate search results.

        Deduplication strategies:

        1. Remove exact ``content_id`` duplicates (identical chunks).
        2. Remove near-duplicate content via word-level Jaccard similarity.

        Word sets for each candidate document are pre-computed once before the
        inner comparison loop so that ``str.split()`` is called exactly once per
        document rather than once per comparison pair.

        Args:
            documents: List of retrieved documents.
            similarity_threshold: Content similarity threshold (0–1) above which
                a document is considered a near-duplicate.

        Returns:
            Deduplicated list of documents in original relevance order.
        """
        if not documents:
            return documents

        seen_ids: set = set()
        deduplicated: list[RetrievedDocument] = []
        # Pre-compute word sets once per document to avoid redundant str.split()
        # calls inside the O(n²) inner similarity loop.
        dedup_word_sets: list[set] = []

        for doc in documents:
            # Fast path: skip exact content-ID duplicates.
            if doc.content_id in seen_ids:
                self.logger.debug(f"Skipping duplicate content ID: {doc.content_id}")
                continue

            # Compute word set for this candidate once.
            candidate_words = set(doc.content.lower().split())

            # Compare against already-accepted documents using pre-built word sets.
            is_duplicate = False
            for existing_words in dedup_word_sets:
                if not candidate_words or not existing_words:
                    continue
                intersection = len(candidate_words & existing_words)
                union = len(candidate_words | existing_words)
                similarity = intersection / union if union > 0 else 0.0

                if similarity >= similarity_threshold:
                    self.logger.debug(f"Skipping near-duplicate content (similarity: {similarity:.2f})")
                    is_duplicate = True
                    break

            if not is_duplicate:
                seen_ids.add(doc.content_id)
                deduplicated.append(doc)
                dedup_word_sets.append(candidate_words)

        if len(deduplicated) < len(documents):
            self.logger.info(f"Deduplication: {len(documents)} -> {len(deduplicated)} documents")

        return deduplicated

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple text strings.

        Args:
            texts: List of input texts to embed

        Returns:
            List of embedding vectors
        """
        try:
            self.logger.debug(f"Generating embeddings for {len(texts)} texts")

            # Get OpenAI client
            client = await self._get_openai_client()

            # Call embedding API with batch of texts
            response = await client.embeddings.create(
                input=texts,
                model=self._openai_options.text_embedding_model,
            )

            # Extract embeddings
            embeddings = [item.embedding for item in response.data]

            self.logger.debug(f"Successfully generated {len(embeddings)} embeddings")

            return embeddings

        except Exception as e:
            self.logger.error(f"Failed to generate embeddings: {e}", exc_info=True)
            raise

    def get_embedding_dimensions(self) -> int:
        """Return the embedding dimension for the configured model.

        Uses the class-level ``_EMBEDDING_DIMENSIONS`` lookup table.
        Falls back to 1536 (``text-embedding-ada-002`` / ``text-embedding-3-small``)
        for unknown models.

        Returns:
            Embedding dimension count.
        """
        model_name = self._openai_options.text_embedding_model
        return self._EMBEDDING_DIMENSIONS.get(model_name, 1536)

    async def close(self):
        """Close the search client."""
        await self._search_client.close()
