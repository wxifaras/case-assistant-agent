"""
Agentic RAG Workflow — core orchestration.

Defines AgenticRAGWorkflow, which inherits the three executor steps from
AgenticRAGExecutors (executors.py) and adds the routing conditions and
build_workflow() entry-point that wire everything together via the
Microsoft Agent Framework WorkflowBuilder.

Flow:
    Search → Reflection → [retry: Search | finalize: Answer]

Decision routing:
- "retry"     + attempts < max  → Loop back to Search with refined query
- "finalize"                    → Proceed to Answer generation
"""

from agent_framework import Workflow, WorkflowBuilder
from agent_framework._workflows._function_executor import FunctionExecutor

from app.workflows.executors import AgenticRAGExecutors


class AgenticRAGWorkflow(AgenticRAGExecutors):
    """
    Agentic RAG Workflow orchestrating iterative retrieval with reflection.

    Inherits the executor steps from AgenticRAGExecutors and adds:
    - should_finalize / should_search routing conditions
    - build_workflow() to assemble and return the runnable Workflow

    See AgenticRAGExecutors for constructor parameters.
    """

    # ------------------------------------------------------------------
    # Routing conditions
    # ------------------------------------------------------------------

    def should_finalize(self):
        """Condition for routing to the answer generator.

        Returns True when the reflection executor has set decision to "finalize".
        """

        def condition(message) -> bool:
            if (
                hasattr(message, "decision")
                and hasattr(message, "current_attempt")
                and hasattr(message, "max_attempts")
            ):
                result = message.decision == "finalize"
                self.logger.info(
                    f"[Condition] should_finalize={result}, "
                    f"decision={message.decision}, "
                    f"attempt={message.current_attempt}/{message.max_attempts}"
                )
                return result
            return False

        return condition

    def should_search(self):
        """Condition for routing back to the search executor.

        Returns True when the reflection executor has set decision to "search".
        """

        def condition(message) -> bool:
            if hasattr(message, "decision"):
                result = message.decision == "search"
                if result:
                    self.logger.info(f"[Condition] should_search=True (decision={message.decision})")
                return result
            return False

        return condition

    # ------------------------------------------------------------------
    # Workflow construction
    # ------------------------------------------------------------------

    def build_workflow(self, *, name: str | None = None) -> Workflow:
        """
        Build and return the configured workflow.

        Args:
            name: Optional workflow name.

        Returns:
            Configured workflow with conditional routing
        """
        self.logger.info("Building Agentic RAG Workflow...")

        # Create function executors from instance methods
        search_exec = FunctionExecutor(self.search_executor, id="search")
        reflection_exec = FunctionExecutor(self.reflection_executor, id="reflection")
        answer_exec = FunctionExecutor(self.answer_generator_executor, id="answer_generator")

        builder = WorkflowBuilder(start_executor=search_exec, name=name)

        # Build workflow with conditional routing
        workflow = (
            builder.add_edge(search_exec, reflection_exec)  # After search, always go to reflection
            .add_edge(reflection_exec, search_exec, condition=self.should_search())  # Loop back if decision == "search"
            .add_edge(
                reflection_exec, answer_exec, condition=self.should_finalize()
            )  # Finalize if decision == "finalize"
            .build()
        )

        self.logger.info("Agentic RAG workflow built: search → reflection → [search | answer_generator]")
        return workflow
