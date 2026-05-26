"""Reflection Agent for evaluating search results and making continuation decisions.

Implements the review/reflection pattern from the reference architecture,
using the MAF ``Agent`` to assess result quality and apply smart-retry
threshold logic before deciding whether to finalize or retry retrieval.
"""

from typing import Any, Literal

from agent_framework import Agent, Message
from agent_framework.openai import OpenAIChatCompletionClient
from azure.identity import DefaultAzureCredential
from opentelemetry import trace

from app.core.logger import Logger
from app.core.settings import Settings
from app.models import RetrievedDocument, ReviewDecision, WorkflowOptions
from app.prompts.templates import ReflectionAgentPrompts


class ReflectionAgent:
    """Agent for reviewing search results and deciding whether to retry or finalize.

    Evaluates the quality of each retrieved document, separates valid from
    invalid results, and applies configurable smart-retry thresholds to
    override the LLM decision when the hit-rate suggests more content exists.
    """

    def __init__(
        self,
        settings: Settings,
        logger: Logger,
        workflow_options: WorkflowOptions,
        credential: DefaultAzureCredential | None = None,
    ):
        """Initialize the reflection agent using MAF Agent.

        Args:
            settings: Application settings with Azure AI configuration.
            logger: Injected logging service.
            workflow_options: Workflow options containing smart-retry thresholds.
            credential: Azure credential for managed identity authentication.
        """
        self.settings = settings
        self.logger = logger
        self.workflow_options = workflow_options
        self.tracer = trace.get_tracer("ReflectionAgent")

        # Initialize MAF Agent

        chat_client = OpenAIChatCompletionClient(
            credential=credential,
            azure_endpoint=settings.azure_openai.endpoint,
            api_version=settings.azure_openai.api_version,
            model=settings.azure_openai.deployment_name,
        )

        self.agent = Agent(
            client=chat_client,
            name="ReflectionAgent",
            instructions=ReflectionAgentPrompts.SEARCH_REVIEW_SYSTEM_PROMPT,
        )

        self.logger.info(f"ReflectionAgent initialized with MAF Agent: {settings.azure_openai.deployment_name}")

    async def review_search_results(
        self,
        user_query: str,
        current_results: list[RetrievedDocument],
        vetted_results: list[RetrievedDocument],
        search_history: list[dict[str, Any]],
        max_attempts: int,
        current_attempt: int,
    ) -> tuple[ReviewDecision, list[RetrievedDocument], list[RetrievedDocument], str]:
        """
        Review search results and determine next action.

        Args:
            user_query: Original user question
            current_results: Results from current search
            vetted_results: Previously approved results
            search_history: History of search attempts with reviews
            max_attempts: Maximum allowed attempts
            current_attempt: Current attempt number

        Returns:
            Tuple of (decision, new_vetted_results, discarded_results, llm_original_decision)
        """
        self.logger.info(f"Reviewing {len(current_results)} search results (attempt {current_attempt}/{max_attempts})")

        try:
            # Format results for review
            current_formatted = self._format_results(current_results)
            vetted_formatted = self._format_results(vetted_results)
            history_formatted = self._format_search_history(search_history)

            # Build review prompt
            review_prompt = ReflectionAgentPrompts.build_review_prompt(
                user_query=user_query,
                current_results_formatted=current_formatted,
                vetted_results_formatted=vetted_formatted,
                vetted_results_count=len(vetted_results),
                search_history_formatted=history_formatted,
                current_results_count=len(current_results),
                current_attempt=current_attempt,
                max_attempts=max_attempts,
            )

            # Get review decision from LLM using MAF Agent with structured output
            message = Message(role="user", contents=[review_prompt])
            response = await self.agent.run(
                messages=[message],
                options={"response_format": ReviewDecision, "temperature": 0.1},
            )

            decision: ReviewDecision | None = response.value

            # If structured parsing failed, try to recover from raw text.
            if decision is None:
                self.logger.warning(
                    "Structured output parsing returned None; attempting JSON fallback parse. "
                    f"Raw response text starts with: {response.text[:200]!r}"
                )
                try:
                    decision = ReviewDecision.model_validate_json(response.text)
                except Exception as parse_exc:
                    raise ValueError(f"Failed to parse ReviewDecision from response text: {parse_exc}") from parse_exc

            # Store original LLM decision before any override
            llm_original_decision = decision.decision

            # Filter valid indices to prevent IndexError
            valid_indices = [idx for idx in decision.valid_results if 0 <= idx < len(current_results)]
            invalid_indices = [idx for idx in decision.invalid_results if 0 <= idx < len(current_results)]

            # Apply smart retry logic
            final_decision = self._apply_smart_retry_logic(
                decision=decision.decision,
                valid_count=len(valid_indices),
                total_count=len(current_results),
                current_attempt=current_attempt,
                max_attempts=max_attempts,
            )

            # Update decision if overridden
            if final_decision != llm_original_decision:
                decision.decision = final_decision

            # Separate valid and invalid results
            new_vetted = [current_results[idx] for idx in valid_indices]
            discarded = [current_results[idx] for idx in invalid_indices]

            self.logger.info(
                f"Review complete: {len(new_vetted)}/{len(current_results)} valid "
                f"({len(new_vetted)/len(current_results)*100:.1f}%), "
                f"LLM decision: {llm_original_decision}, Final decision: {decision.decision}"
            )

            return decision, new_vetted, discarded, llm_original_decision

        except Exception as e:
            self.logger.error(f"Review failed: {e}")
            # Fallback: accept all results and finalize
            fallback_decision = ReviewDecision(
                thought_process=f"Review failed: {str(e)}. Accepting all results.",
                valid_results=list(range(len(current_results))),
                invalid_results=[],
                decision="finalize",
            )
            return fallback_decision, current_results, [], "finalize"

    def _format_results(self, results: list[RetrievedDocument]) -> str:
        """Format retrieved documents for display in the review prompt.

        Args:
            results: Documents to format.

        Returns:
            Multi-line string with numbered result blocks.
        """
        if not results:
            return "No results available."

        parts = []
        for i, doc in enumerate(results):
            parts.append(f"\nResult #{i}")
            parts.append("=" * 80)
            parts.append(f"Content ID: {doc.content_id}")
            parts.append(f"Document ID: {doc.document_id}")
            parts.append(f"Title: {doc.title}")
            parts.append(f"Score: {doc.score:.4f}")
            if doc.reranker_score is not None:
                parts.append(f"Reranker Score: {doc.reranker_score:.4f}")
            parts.append("\n--- Content ---")
            parts.append(doc.content[:500] + ("..." if len(doc.content) > 500 else ""))
            parts.append("--- End Content ---")
            parts.append("-" * 80)

        return "\n".join(parts)

    def _format_search_history(self, search_history: list[dict[str, Any]]) -> str:
        """Format prior search attempts for inclusion in the review prompt.

        Args:
            search_history: List of attempt dicts, each with ``query`` and ``review`` keys.

        Returns:
            Multi-line string summarising all previous attempts.
        """
        if not search_history:
            return "No previous search attempts."

        parts = ["\n=== Search History ==="]
        for i, entry in enumerate(search_history, 1):
            parts.append(f"\n<Attempt {i}>")
            parts.append(f"Query: {entry.get('query', '')}")
            parts.append(f"Review: {entry.get('review', '')}")
            parts.append("</Attempt>")

        return "\n".join(parts)

    def _apply_smart_retry_logic(
        self,
        decision: Literal["retry", "finalize"],
        valid_count: int,
        total_count: int,
        current_attempt: int,
        max_attempts: int,
    ) -> Literal["retry", "finalize"]:
        """Apply intelligent retry logic to override LLM decision if needed.

        Overrides ``finalize`` to ``retry`` when the hit-rate is high enough
        that more relevant content likely exists in the index.

        Args:
            decision: LLM-chosen action (``"retry"`` or ``"finalize"``).
            valid_count: Number of documents marked valid in this round.
            total_count: Total documents reviewed in this round.
            current_attempt: Current iteration number (1-based).
            max_attempts: Maximum allowed iterations.

        Returns:
            Final action after applying smart-retry overrides.
        """
        if total_count == 0:
            return "retry" if current_attempt < max_attempts else "finalize"

        valid_percentage = valid_count / total_count

        high = self.workflow_options.reflection_high_validity_threshold
        moderate = self.workflow_options.reflection_moderate_validity_threshold
        moderate_min = self.workflow_options.reflection_moderate_validity_min_count

        # Override finalize if we're finding lots of valid content (suggests more exists)
        if decision == "finalize" and current_attempt < max_attempts:
            if valid_percentage >= high:
                self.logger.info(
                    f"Overriding 'finalize': High validity rate ({valid_percentage:.1%}) suggests more content available"
                )
                return "retry"
            elif valid_percentage >= moderate and valid_count >= moderate_min:
                self.logger.info(
                    f"Overriding 'finalize': Good validity ({valid_percentage:.1%}) with {valid_count} results suggests comprehensive search needed"
                )
                return "retry"

        return decision
