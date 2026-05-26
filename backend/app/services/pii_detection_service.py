"""Azure AI Language PII detection and redaction service.

This module provides PII (Personally Identifiable Information) detection and
redaction for prompts, documents, and LLM outputs using the Azure AI Language
Text Analytics service.

While Azure OpenAI's built-in content filters can detect PII in model
outputs (see
https://learn.microsoft.com/azure/foundry/openai/concepts/content-filter-personal-information),
that filter only runs inline with a completion call and cannot be invoked
against an arbitrary string — for example, to pre-screen a user prompt before
it reaches the LLM, or to redact documents before indexing them in Azure AI
Search. This service wraps the underlying Azure AI Language PII API directly
so callers can:

    - Pre-screen user prompts before sending them to the LLM
    - Redact PII from documents/chunks before indexing or logging
    - Post-process LLM outputs as defense-in-depth alongside content filters

Supported entity categories include Email, PhoneNumber, Address, Person,
IPAddress, DateOfBirth, DriversLicenseNumber, PassportNumber,
CreditCardNumber, BankAccountNumber, SWIFTCode, IBAN, USSocialSecurityNumber,
national IDs for 50+ countries, and Azure-related secrets (connection
strings, storage keys). For the full list, see
https://learn.microsoft.com/azure/ai-services/language-service/personally-identifiable-information/concepts/entity-categories

Features:
    - Async client with lazy initialization (matches BlobStorageService)
    - DefaultAzureCredential or API key authentication
    - Per-category filtering (categories_filter)
    - Confidence-score threshold (min_confidence)
    - Batch processing for multiple documents
    - Exponential-backoff retry on 429 / transient errors
    - Redaction and boolean `contains_pii` helpers
    - `close()` method to release underlying connections

Usage:
    pii_service = PIIDetectionService(settings, logger)
    result = await pii_service.detect_pii_async(
        "Contact me at john@example.com or call 555-0123"
    )
    if result.contains_pii:
        safe_text = result.redacted_text
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from azure.ai.textanalytics.aio import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.identity.aio import DefaultAzureCredential
from pydantic import BaseModel, Field

from app.core.logger import Logger
from app.core.settings import Settings

# Default PII categories surfaced by Azure AI Language. Kept as a module-level
# constant so callers (and tests) can import and override without touching the
# service internals.
DEFAULT_PII_CATEGORIES: tuple[str, ...] = (
    "Person",
    "PersonType",
    "PhoneNumber",
    "Email",
    "Address",
    "IPAddress",
    "URL",
    "DateOfBirth",
    "CreditCardNumber",
    "BankAccountNumber",
    "SWIFTCode",
    "IBAN",
    "USSocialSecurityNumber",
    "DriversLicenseNumber",
    "PassportNumber",
    "AzureDocumentDBAuthKey",
    "AzureIAASDatabaseConnectionAndSQLString",
    "AzureIoTConnectionString",
    "AzureRedisCacheString",
    "AzureSAS",
    "AzureServiceBusString",
    "AzureStorageAccountKey",
    "AzureStorageAccountGeneric",
)


class PIIEntity(BaseModel):
    """A single PII entity detected in text."""

    text: str = Field(..., description="The PII text as it appeared in the source")
    category: str = Field(..., description="Entity category, e.g. 'Email', 'PhoneNumber'")
    subcategory: str | None = Field(default=None, description="Optional entity subcategory")
    offset: int = Field(..., description="Character offset of the entity in the source text")
    length: int = Field(..., description="Length of the entity in characters")
    confidence_score: float = Field(..., description="Model confidence (0.0 – 1.0)")


class PIIDetectionResult(BaseModel):
    """Structured result of a PII detection call."""

    entities: list[PIIEntity] = Field(default_factory=list, description="All PII entities found")
    redacted_text: str = Field(..., description="Input text with PII replaced by '*' characters")
    original_length: int = Field(..., description="Length of the original input text")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal service warnings")

    @property
    def contains_pii(self) -> bool:
        """True if any PII entity was detected."""
        return len(self.entities) > 0


class IPIIDetectionService(ABC):
    """Interface for PII detection service operations."""

    @abstractmethod
    async def detect_pii_async(
        self,
        text: str,
        *,
        language: str = "en",
        categories_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> PIIDetectionResult:
        """Detect PII entities in a single text string."""
        pass

    @abstractmethod
    async def detect_pii_batch_async(
        self,
        texts: list[str],
        *,
        language: str = "en",
        categories_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> list[PIIDetectionResult]:
        """Detect PII entities in multiple text strings."""
        pass

    @abstractmethod
    async def redact_pii_async(
        self,
        text: str,
        *,
        language: str = "en",
        categories_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> str:
        """Return `text` with any detected PII replaced by `*` characters."""
        pass

    @abstractmethod
    async def contains_pii_async(
        self,
        text: str,
        *,
        language: str = "en",
        categories_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> bool:
        """Return True if `text` contains any PII at or above `min_confidence`."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Release the underlying client and credential."""
        pass


class PIIDetectionService(IPIIDetectionService):
    """Async PII detection service backed by Azure AI Language.

    The underlying `TextAnalyticsClient` and `DefaultAzureCredential` are
    created lazily on first use so the API can start up even when the
    Language endpoint is not yet configured — mirroring the lazy-init
    pattern used by `BlobStorageService`.
    """

    def __init__(
        self,
        settings: Settings,
        logger: Logger | None = None,
    ) -> None:
        """Initialize the PII detection service.

        Args:
            settings: Application settings. Expects `settings.pii_detection`
                with at minimum an `endpoint` attribute and an optional
                `api_key`.
            logger: Optional logger; a default is created when omitted.

        Note:
            Clients are created lazily on first use to allow API startup
            even when the Language endpoint is not configured.
        """
        self._settings: Settings = settings
        self.logger: Logger = logger or Logger()
        self._credential: DefaultAzureCredential | None = None
        self._client: TextAnalyticsClient | None = None

    def _ensure_client(self) -> TextAnalyticsClient:
        """Ensure the TextAnalyticsClient is initialized."""
        if self._client is not None:
            return self._client

        options = getattr(self._settings, "pii_detection", None)
        endpoint = getattr(options, "endpoint", None) if options else None
        if not endpoint:
            raise ValueError("PII detection endpoint is not configured (settings.pii_detection.endpoint)")

        api_key = getattr(options, "api_key", None) if options else None
        if api_key:
            self._client = TextAnalyticsClient(
                endpoint=endpoint,
                credential=AzureKeyCredential(api_key),
            )
        else:
            # Passwordless auth via managed identity / DefaultAzureCredential,
            # matching the approach in BlobStorageService and SearchService.
            self._credential = DefaultAzureCredential()
            self._client = TextAnalyticsClient(
                endpoint=endpoint,
                credential=self._credential,
            )

        self.logger.info(f"PIIDetectionService initialized for endpoint: {endpoint}")
        return self._client

    async def close(self) -> None:
        """Close the TextAnalyticsClient and credential, releasing all connections."""
        if self._client:
            await self._client.close()
        if self._credential:
            await self._credential.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def detect_pii_async(
        self,
        text: str,
        *,
        language: str = "en",
        categories_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> PIIDetectionResult:
        """Detect PII entities in a single text string.

        Args:
            text: Input text to scan. Empty strings short-circuit to an
                empty result.
            language: BCP-47 language code (default: "en").
            categories_filter: When provided, only entities whose category
                is in this list are returned.
            min_confidence: Drop any entity whose `confidence_score` is
                below this threshold (0.0 – 1.0).

        Returns:
            PIIDetectionResult with detected entities and a redacted copy
            of the input.

        Example:
            >>> result = await pii_service.detect_pii_async(
            ...     "Email me at jane@example.com"
            ... )
            >>> result.contains_pii
            True
            >>> result.redacted_text
            'Email me at ****************'
        """
        if not text:
            return PIIDetectionResult(redacted_text="", original_length=0)

        results = await self._recognize_with_retry(
            documents=[text],
            language=language,
            categories_filter=categories_filter,
        )
        return self._parse_single_result(
            raw_result=results[0],
            source_text=text,
            min_confidence=min_confidence,
            categories_filter=categories_filter,
        )

    async def detect_pii_batch_async(
        self,
        texts: list[str],
        *,
        language: str = "en",
        categories_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> list[PIIDetectionResult]:
        """Detect PII in multiple text strings in a single request.

        The Azure AI Language PII endpoint supports batching, which is much
        cheaper than serial calls when processing many chunks (e.g. before
        indexing).

        Args:
            texts: List of input texts. Empty entries are preserved as
                empty results.
            language: BCP-47 language code (default: "en").
            categories_filter: When provided, only entities whose category
                is in this list are returned.
            min_confidence: Drop entities below this confidence threshold.

        Returns:
            One PIIDetectionResult per input, in the same order.
        """
        if not texts:
            return []

        # Azure AI Language rejects empty documents; substitute a sentinel
        # and stitch empty results back in after the call.
        non_empty_indices: list[int] = [i for i, t in enumerate(texts) if t]
        non_empty_texts: list[str] = [texts[i] for i in non_empty_indices]

        if not non_empty_texts:
            return [PIIDetectionResult(redacted_text="", original_length=0) for _ in texts]

        raw_results = await self._recognize_with_retry(
            documents=non_empty_texts,
            language=language,
            categories_filter=categories_filter,
        )

        parsed: list[PIIDetectionResult] = [PIIDetectionResult(redacted_text="", original_length=0) for _ in texts]
        for idx, raw in zip(non_empty_indices, raw_results, strict=True):
            parsed[idx] = self._parse_single_result(
                raw_result=raw,
                source_text=texts[idx],
                min_confidence=min_confidence,
                categories_filter=categories_filter,
            )
        return parsed

    async def redact_pii_async(
        self,
        text: str,
        *,
        language: str = "en",
        categories_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> str:
        """Return `text` with any detected PII replaced by `*` characters.

        Convenience wrapper around `detect_pii_async` that returns only
        the redacted string.
        """
        result = await self.detect_pii_async(
            text,
            language=language,
            categories_filter=categories_filter,
            min_confidence=min_confidence,
        )
        return result.redacted_text

    async def contains_pii_async(
        self,
        text: str,
        *,
        language: str = "en",
        categories_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> bool:
        """Return True if `text` contains any PII above `min_confidence`."""
        result = await self.detect_pii_async(
            text,
            language=language,
            categories_filter=categories_filter,
            min_confidence=min_confidence,
        )
        return result.contains_pii

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _recognize_with_retry(
        self,
        documents: list[str],
        language: str,
        categories_filter: list[str] | None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> list[Any]:
        """Call `recognize_pii_entities` with exponential-backoff retry.

        Mirrors the retry strategy in `SearchService._search_with_retry`
        so operational behaviour is consistent across services.

        Args:
            documents: Non-empty list of documents to analyse.
            language: BCP-47 language code.
            categories_filter: Optional category filter forwarded to the
                Azure SDK.
            max_retries: Maximum retry attempts on transient errors.
            base_delay: Base delay in seconds for exponential backoff.

        Returns:
            List of recognize_pii_entities result items (may include
            `DocumentError` entries for per-doc failures).
        """
        client = self._ensure_client()

        kwargs: dict[str, Any] = {"documents": documents, "language": language}
        if categories_filter:
            kwargs["categories_filter"] = categories_filter

        for attempt in range(max_retries + 1):
            try:
                response = await client.recognize_pii_entities(**kwargs)
                # The SDK returns an ItemPaged / list of per-document results.
                return list(response)

            except HttpResponseError as e:
                if getattr(e, "status_code", None) == 429 and attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    self.logger.warning(
                        f"PII detection rate limited, retrying in {delay}s " f"(attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                else:
                    self.logger.error(f"PII detection failed: {e}", exc_info=True)
                    raise

            except Exception as e:
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    self.logger.warning(
                        f"PII detection failed, retrying in {delay}s " f"(attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    self.logger.error(f"PII detection failed after retries: {e}", exc_info=True)
                    raise

        raise RuntimeError(f"PII detection failed after {max_retries} retries")

    def _parse_single_result(
        self,
        raw_result: Any,
        source_text: str,
        min_confidence: float,
        categories_filter: list[str] | None,
    ) -> PIIDetectionResult:
        """Convert a raw SDK result into a `PIIDetectionResult`.

        Handles both successful documents and per-document errors. Applies
        the client-side `min_confidence` threshold (the SDK filter operates
        only on categories).

        Args:
            raw_result: A single element from `recognize_pii_entities`
                (either a success item or a `DocumentError`).
            source_text: The original input text for this document.
            min_confidence: Drop entities below this score.
            categories_filter: If provided, defensively drop any entity
                whose category isn't in the filter (the SDK usually does
                this server-side, but we guard against SDK drift).

        Returns:
            Parsed `PIIDetectionResult`.
        """
        # Per-document errors are surfaced as objects with `is_error == True`.
        if getattr(raw_result, "is_error", False):
            err = getattr(raw_result, "error", None)
            err_msg = getattr(err, "message", "Unknown PII detection error") if err else "Unknown error"
            self.logger.warning(f"[PIIDetectionService] Document error: {err_msg}")
            return PIIDetectionResult(
                redacted_text=source_text,
                original_length=len(source_text),
                warnings=[err_msg],
            )

        entities: list[PIIEntity] = []
        for e in getattr(raw_result, "entities", []) or []:
            if e.confidence_score < min_confidence:
                continue
            if categories_filter and e.category not in categories_filter:
                continue
            entities.append(
                PIIEntity(
                    text=e.text,
                    category=str(e.category),
                    subcategory=getattr(e, "subcategory", None),
                    offset=e.offset,
                    length=e.length,
                    confidence_score=e.confidence_score,
                )
            )

        # Prefer the SDK-provided redacted_text when available; otherwise
        # build it from detected offsets. We rebuild when client-side
        # filtering (min_confidence / categories_filter) trimmed entities,
        # so the redacted output matches what we actually report.
        sdk_redacted = getattr(raw_result, "redacted_text", None)
        sdk_entity_count = len(getattr(raw_result, "entities", []) or [])
        if sdk_redacted is not None and len(entities) == sdk_entity_count:
            redacted_text = sdk_redacted
        else:
            redacted_text = self._redact_from_entities(source_text, entities)

        warnings = [str(w) for w in (getattr(raw_result, "warnings", []) or [])]

        return PIIDetectionResult(
            entities=entities,
            redacted_text=redacted_text,
            original_length=len(source_text),
            warnings=warnings,
        )

    @staticmethod
    def _redact_from_entities(text: str, entities: list[PIIEntity]) -> str:
        """Replace each entity span in `text` with `*` characters.

        Processes entities in reverse offset order so earlier offsets remain
        valid while we splice — a common trick when applying in-place
        replacements to a plain string.
        """
        if not entities:
            return text
        chars = list(text)
        for e in sorted(entities, key=lambda x: x.offset, reverse=True):
            start = e.offset
            end = min(len(chars), start + e.length)
            chars[start:end] = ["*"] * (end - start)
        return "".join(chars)
