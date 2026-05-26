"""
Agentic RAG Workflow Executors.

Contains the AgenticRAGExecutors base class, which implements the three workflow
executor steps (search, reflection, answer generation). This class is intended
to be subclassed by AgenticRAGWorkflow in core.py, which adds routing
conditions and the build_workflow() entry-point.
"""

from agent_framework import WorkflowContext

from app.agents.answer_generator import AnswerGenerator
from app.agents.query_rewriter import QueryRewriter
from app.agents.reflection_agent import ReflectionAgent
from app.core.logger import Logger
from app.core.settings import Settings
from app.models import PIIDetectionOptions, WorkflowOptions
from app.models.chat import AgenticRAGState
from app.prompts.templates import AnswerGeneratorPrompts
from app.services.pii_detection_service import IPIIDetectionService
from app.services.search_service import ISearchService
from app.utils.citation_tracker import CitationTracker


class AgenticRAGExecutors:
    """
    Executor steps for the Agentic RAG Workflow.

    Provides the three async executor methods called by the workflow runtime
    (search, reflection, answer generation). Routing conditions and workflow
    construction live in the AgenticRAGWorkflow subclass (core.py).
    """

    def __init__(
        self,
        settings: Settings,
        logger: Logger,
        workflow_options: WorkflowOptions,
        search_service: ISearchService,
        citation_tracker: CitationTracker,
        query_rewriter: QueryRewriter,
        answer_generator: AnswerGenerator,
        reflection_agent: ReflectionAgent,
        pii_detection_service: IPIIDetectionService | None = None,
        pii_detection_options: PIIDetectionOptions | None = None,
    ):
        """
        Initialize executors with all required dependencies.

        Args:
            settings: Application settings
            logger: Logging service
            workflow_options: Workflow execution configuration
            search_service: Search service interface for hybrid search operations
            citation_tracker: Citation tracking utility
            query_rewriter: Query rewriting agent
            answer_generator: Answer generation agent
            reflection_agent: Reflection agent for result review
            pii_detection_service: Optional PII detection service
            pii_detection_options: Optional PII handling configuration
        """
        self.settings = settings
        self.logger = logger
        self.workflow_options = workflow_options
        self.search_service = search_service
        self.citation_tracker = citation_tracker
        self.query_rewriter = query_rewriter
        self.answer_generator = answer_generator
        self.reflection_agent = reflection_agent
        self.pii_detection_service = pii_detection_service
        self.pii_detection_options = pii_detection_options

    def _resolve_pii_mode(self) -> str:
        """Resolve effective PII mode while supporting legacy option fields."""
        if not (self.pii_detection_service and self.pii_detection_options and self.pii_detection_options.enabled):
            return "off"

        mode = (getattr(self.pii_detection_options, "mode", "") or "").lower()
        if mode in {"block", "redact", "detect"}:
            return mode

        if getattr(self.pii_detection_options, "block_on_detection", False):
            return "block"
        if getattr(self.pii_detection_options, "redact_responses", False):
            return "redact"
        return "detect"

    # ------------------------------------------------------------------
    # Executor steps
    # ------------------------------------------------------------------

    async def search_executor(self, state: AgenticRAGState, ctx: WorkflowContext[AgenticRAGState]) -> None:
        """Execute search iteration with HyDE query rewriting."""
        state.current_attempt += 1
        self.logger.info(f"[Search] EXECUTOR CALLED - Attempt {state.current_attempt}/{state.max_attempts}")

        pii_mode = self._resolve_pii_mode()
        if pii_mode != "off" and self.pii_detection_service:
            try:
                pii_result = await self.pii_detection_service.detect_pii_async(
                    state.query,
                    language=getattr(self.pii_detection_options, "language", "en"),
                    min_confidence=getattr(self.pii_detection_options, "min_confidence", 0.0),
                    categories_filter=getattr(self.pii_detection_options, "categories_filter", None),
                )
                if pii_result.contains_pii:
                    categories = sorted({entity.category for entity in pii_result.entities})
                    self.logger.warning(
                        f"[PII Guard] Detected {len(pii_result.entities)} entities in workflow query: {categories}"
                    )

                    if pii_mode == "block":
                        state.answer = (
                            "I noticed your message appears to contain personal or sensitive information "
                            f"({', '.join(categories)}). Please remove that information and try again."
                        )
                        state.citations = []
                        state.current_results = []
                        state.decision = "finalize"
                        state.decisions.append("pii_blocked")
                        state.thought_process.append(
                            {
                                "step": "pii_guard",
                                "details": {
                                    "mode": pii_mode,
                                    "blocked": True,
                                    "entity_count": len(pii_result.entities),
                                    "categories": categories,
                                },
                            }
                        )
                        await ctx.yield_output(state)  # type: ignore[attr-defined]
                        return

                    if pii_mode == "redact" and pii_result.redacted_text:
                        state.query = pii_result.redacted_text
                        state.thought_process.append(
                            {
                                "step": "pii_guard",
                                "details": {
                                    "mode": pii_mode,
                                    "blocked": False,
                                    "redacted_query": True,
                                    "entity_count": len(pii_result.entities),
                                    "categories": categories,
                                },
                            }
                        )
            except Exception as e:
                self.logger.error(f"[PII Guard] Query scan failed; continuing without blocking: {e}")

        # Generate HyDE query if enabled, otherwise use original query
        if self.workflow_options.enable_query_rewriting:
            search_query = await self.query_rewriter.generate_hyde_search_query(
                user_query=state.query, search_history=state.search_history, previous_reviews=state.previous_reviews
            )
        else:
            search_query = state.query
            self.logger.info("[Search] Query rewriting disabled, using original query")

        try:
            # Execute search with filters if they exist, otherwise search without filters.
            # Exclude already processed content_ids to maximise unique results each iteration.
            results = await self.search_service.search_async(
                query=search_query,
                search_mode="hybrid",
                top_k=10,
                filters=state.filters,
                exclude_ids=list(state.processed_content_ids),
            )

            state.current_results = results
            state.search_history.append(
                {"query": search_query, "results_count": len(results), "attempt": state.current_attempt}
            )

            state.thought_process.append(
                {
                    "step": "retrieve",
                    "details": {
                        "attempt": f"{state.current_attempt} out of {state.max_attempts}",
                        "user_query": state.query,
                        "generated_search_query": search_query,
                        "applied_filters": (
                            ({k: v for k, v in state.filters.items() if v} or None) if state.filters else None
                        ),
                        "results_summary": [
                            {
                                "content_id": result.content_id,
                                "document_id": result.document_id,
                                "title": result.title,
                                "score": result.score,
                                "reranker_score": result.reranker_score,
                                "content": result.content,
                            }
                            for result in results
                        ],
                    },
                }
            )

            self.logger.info(f"[Search] Found {len(results)} results")

        except Exception as e:
            self.logger.error(f"[Search] Failed: {e}")
            state.current_results = []

        # Send state to next executor (reflection)
        await ctx.send_message(state)

    async def reflection_executor(self, state: AgenticRAGState, ctx: WorkflowContext[AgenticRAGState]) -> None:
        """Review search results and decide whether to continue or finalize."""
        self.logger.info(f"[Reflection] EXECUTOR CALLED - Reviewing {len(state.current_results)} results")

        if not state.current_results:
            self.logger.warning("[Reflection] No results to review, finalizing with empty answer")
            state.decision = "finalize"
            await ctx.send_message(state)
            return

        try:
            # Use ReflectionAgent to review results
            decision, new_vetted, discarded, llm_original_decision = await self.reflection_agent.review_search_results(
                user_query=state.query,
                current_results=state.current_results,
                vetted_results=state.vetted_results,
                search_history=state.search_history,
                max_attempts=state.max_attempts,
                current_attempt=state.current_attempt,
            )

            # Update state with reviewed results
            state.vetted_results.extend(new_vetted)
            state.discarded_results.extend(discarded)
            state.previous_reviews.append(decision.thought_process)

            # Store final decision (after smart retry logic)
            final_decision = decision.decision
            state.decisions.append(final_decision)

            # Mark all current results as processed (by content_id to track unique chunks)
            for doc in state.current_results:
                state.processed_content_ids.add(doc.content_id)

            # Calculate metrics for logging
            current_count = len(state.current_results)
            valid_count = len(new_vetted)
            valid_percentage = valid_count / current_count if current_count > 0 else 0

            # Log thought process
            state.thought_process.append(
                {
                    "step": "review",
                    "details": {
                        "attempt": f"{state.current_attempt} out of {state.max_attempts}",
                        "review_thought_process": decision.thought_process,
                        "valid_results": [
                            {
                                "content_id": doc.content_id,
                                "document_id": doc.document_id,
                                "title": doc.title,
                                "score": doc.score,
                                "reranker_score": doc.reranker_score,
                                "content": doc.content,
                            }
                            for doc in new_vetted
                        ],
                        "invalid_results": [
                            {
                                "content_id": doc.content_id,
                                "document_id": doc.document_id,
                                "title": doc.title,
                                "score": doc.score,
                                "reranker_score": doc.reranker_score,
                                "content": doc.content,
                            }
                            for doc in discarded
                        ],
                        "llm_decision": llm_original_decision,
                        "final_decision": final_decision,
                        "decision_override": final_decision != llm_original_decision,
                        "valid_count": valid_count,
                        "invalid_count": len(discarded),
                        "valid_percentage": f"{valid_percentage:.0%}",
                        "total_vetted": len(state.vetted_results),
                    },
                }
            )

            # Clear current results for next iteration
            state.current_results = []

            # Route based on decision
            if decision.decision == "retry" and state.current_attempt < state.max_attempts:
                state.decision = "search"
                self.logger.info(
                    f"[Reflection] ROUTING DECISION: search (continue iteration {state.current_attempt}/{state.max_attempts})"
                )
            else:
                state.decision = "finalize"
                self.logger.info(
                    f"[Reflection] ROUTING DECISION: finalize with {len(state.vetted_results)} vetted results"
                )

        except Exception as e:
            self.logger.error(f"[Reflection] Failed: {e}")
            state.decision = "finalize"

        # Send state to next executor based on decision
        await ctx.send_message(state)

    async def answer_generator_executor(self, state: AgenticRAGState, ctx: WorkflowContext[AgenticRAGState]) -> None:
        """Generate final answer from vetted results."""
        self.logger.info(
            f"[AnswerGenerator] EXECUTOR CALLED - Generating from {len(state.vetted_results)} vetted results"
        )

        try:
            vetted_results_formatted = ""
            for i, doc in enumerate(state.vetted_results, 1):
                result_parts = [
                    f"\nResult #{i}",
                    "=" * 80,
                    f"Content ID: {doc.content_id}",
                    f"Document ID: {doc.document_id}",
                    f"Title: {doc.title}",
                    f"Source: {doc.source}",
                    f"Page Number: {doc.page_number if doc.page_number else 'N/A'}",
                    "\n<Start Content>",
                    "-" * 80,
                    doc.content,
                    "-" * 80,
                    "<End Content>",
                ]
                vetted_results_formatted += "\n".join(result_parts)

            # Build the answer prompt using template
            generated_answer_prompt = AnswerGeneratorPrompts.build_answer_prompt(
                query=state.query, vetted_results_formatted=vetted_results_formatted
            )

            # Generate answer using answer_generator with the answer prompt that includes
            # citation instructions and formatted vetted results
            generated_answer = await self.answer_generator.generate_answer(
                query=state.query,
                documents=state.vetted_results,
                generated_answer_prompt=generated_answer_prompt,
                conversation_history=state.chat_history,
            )

            state.answer = generated_answer.answer_text
            state.citations = generated_answer.citations

            pii_mode = self._resolve_pii_mode()
            should_redact_answer = pii_mode == "redact" or getattr(
                self.pii_detection_options, "redact_responses", False
            )
            if should_redact_answer and self.pii_detection_service and state.answer:
                try:
                    state.answer = await self.pii_detection_service.redact_pii_async(
                        state.answer,
                        language=getattr(self.pii_detection_options, "language", "en"),
                        min_confidence=getattr(self.pii_detection_options, "min_confidence", 0.0),
                        categories_filter=getattr(self.pii_detection_options, "categories_filter", None),
                    )
                except Exception as e:
                    self.logger.error(f"[PII Guard] Answer redaction failed; returning original answer: {e}")

            # If we had to bypass filters, add a transparency note to the answer
            if getattr(state, "searched_without_filters", False):
                state.answer += (
                    "\n\n---\n" "**Note:** This answer was generated by searching across all available documents."
                )

            state.thought_process.append(
                {
                    "step": "response",
                    "details": {
                        "final_answer": state.answer,
                        "citations_count": len(state.citations),
                        "cited_documents": state.citations,
                    },
                }
            )

            self.logger.info(f"[AnswerGenerator] Complete with {len(state.citations or [])} citations")

        except Exception as e:
            self.logger.error(f"[AnswerGenerator] Failed: {e}")
            state.answer = f"I encountered an error generating the final answer. Error: {str(e)}. Please try rephrasing your question."
            state.citations = []

            state.thought_process.append(
                {"step": "response", "details": {"final_answer": state.answer, "error": str(e)}}
            )

        # Yield final output
        await ctx.yield_output(state)  # type: ignore[attr-defined]
