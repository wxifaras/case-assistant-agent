"""Query Rewriter Agent for multi-strategy query expansion and rewriting.

Uses the MAF ``Agent`` and HyDE (Hypothetical Document Embedding) to
generate hypothetical passages that represent expected answer content,
improving semantic-search recall for the agentic RAG workflow.
"""

from typing import Any

from agent_framework import Agent, Message
from agent_framework.openai import OpenAIChatCompletionClient
from azure.identity import DefaultAzureCredential
from opentelemetry import trace

from app.core.logger import Logger
from app.core.settings import Settings
from app.models.chat import RewrittenQuery
from app.prompts import QueryRewriterPrompts


class QueryRewriter:
    """Agent for HyDE (Hypothetical Document Embedding) query generation.

    Generates hypothetical document passages for semantic search, creating
    search queries that represent what the answer content might look like
    rather than traditional keyword-based queries.
    """

    def __init__(self, settings: Settings, logger: Logger, credential: DefaultAzureCredential | None = None):
        """
        Initialize the query rewriter agent using MAF Agent.

        Args:
            settings: Application settings with Azure AI configuration
            logger: Injected logging service
            credential: Azure credential for managed identity authentication
        """
        self.settings = settings
        self.logger = logger
        self.tracer = trace.get_tracer("QueryRewriterAgent")
        self._hyde_temperature: float = settings.workflow.hyde_temperature
        self._hyde_max_tokens: int = settings.workflow.hyde_max_tokens

        # Initialize MAF Agent
        chat_client = OpenAIChatCompletionClient(
            credential=credential,
            azure_endpoint=settings.azure_openai.endpoint,
            api_version=settings.azure_openai.api_version,
            model=settings.azure_openai.deployment_name,
        )

        self.agent = Agent(
            client=chat_client, name="QueryRewriterAgent", instructions=QueryRewriterPrompts.HYDE_SYSTEM_PROMPT
        )

        self.logger.info(f"QueryRewriter initialized with MAF Agent: {settings.azure_openai.deployment_name}")

    async def generate_hyde_search_query(
        self,
        user_query: str,
        search_history: list[dict[str, Any]] | None = None,
        previous_reviews: list[str] | None = None,
    ) -> str:
        """
        Generate HyDE (Hypothetical Document Embedding) search query.
        Creates a hypothetical paragraph of what the answer/content might look like.
        For subsequent searches, diversifies strategy based on previous attempts.

        Args:
            user_query: Original user question
            search_history: List of previous search attempts with queries
            previous_reviews: List of review feedback from previous searches

        Returns:
            HyDE search query text
        """
        query_preview = user_query[:100] + ("..." if len(user_query) > 100 else "")
        self.logger.info(f"Generating HyDE search query for: {query_preview}")

        # Build context for prompt
        context_parts = [f"User Question: {user_query}"]

        # Add search history for subsequent attempts
        if search_history and previous_reviews:
            context_parts.append("\n### Previous Search Attempts ###")
            for i, (search, review) in enumerate(zip(search_history, previous_reviews, strict=False), 1):
                context_parts.append(f"\n<Attempt {i}>")
                context_parts.append(f"Query: {search.get('query', '')}")
                context_parts.append(f"Review: {review}")
                context_parts.append("</Attempt>")

            context_parts.append("\nCRITICAL: Since this is NOT the first search, you MUST diversify your approach:")
            context_parts.append("- Use different terminology, synonyms, or technical vs. layman terms")
            context_parts.append("- Focus on different aspects, time periods, or perspectives")
            context_parts.append("- Explore related concepts, causes, effects, or stakeholder viewpoints")

        context_parts.append("\nGenerate a hypothetical paragraph of what you expect to find in the target documents.")
        context_parts.append("Make it sound like the actual content, NOT like a search query.")

        try:
            context_text = "\n".join(context_parts)
            user_prompt = context_text

            rewritten_query = await self._call_llm(user_prompt)

            passage_preview = rewritten_query.hypothetical_passage[:150] + (
                "..." if len(rewritten_query.hypothetical_passage) > 150 else ""
            )
            self.logger.debug(f"Generated HyDE query: {passage_preview}")
            self.logger.debug(f"Reasoning: {rewritten_query.reasoning}")

            return rewritten_query.hypothetical_passage

        except Exception as e:
            self.logger.error(f"HyDE generation failed: {e}")
            # Fallback to original query
            return user_query

    async def _call_llm(self, user_prompt: str) -> RewrittenQuery:
        """
        Call LLM for query rewriting using MAF Agent.

        Args:
            user_prompt: User message for query rewriting

        Returns:
            RewrittenQuery model with hypothetical_passage and reasoning
        """
        # Create MAF Message
        message = Message(role="user", contents=[user_prompt])

        # Run agent with JSON mode for structured output
        result = await self.agent.run(
            messages=[message],
            options={
                "response_format": RewrittenQuery,
                "max_tokens": self._hyde_max_tokens,
                "temperature": self._hyde_temperature,
            },
        )

        # Parse the structured response
        return RewrittenQuery.model_validate_json(result.text)
