"""Chat/workflow provider group builder for the DI container."""

from __future__ import annotations

from dependency_injector import providers

from app.agents.answer_generator import AnswerGenerator
from app.agents.query_rewriter import QueryRewriter
from app.agents.reflection_agent import ReflectionAgent
from app.services.chat_history_service import ChatHistoryService, IChatHistoryService
from app.services.chat_service import ChatService, IChatService
from app.services.foundry_service import FoundryService, IFoundryService
from app.services.pii_detection_service import IPIIDetectionService, PIIDetectionService
from app.utils.citation_tracker import CitationTracker
from app.workflows.core import AgenticRAGWorkflow


def build_chat_workflow_providers(
    *,
    config,
    logger,
    workflow_options,
    azure_credential,
    search_service,
    foundry_agent_options,
    pii_detection_options,
    cosmos_repository,
) -> tuple[
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
    providers.Provider,
]:
    """Create providers for chat history, workflow agents, and chat service."""
    citation_tracker: providers.Factory[CitationTracker] = providers.Factory(
        CitationTracker,
        logger=logger,
    )

    query_rewriter: providers.Factory[QueryRewriter] = providers.Factory(
        QueryRewriter,
        settings=config,
        logger=logger,
        credential=azure_credential,
    )

    reflection_agent: providers.Factory[ReflectionAgent] = providers.Factory(
        ReflectionAgent,
        settings=config,
        logger=logger,
        workflow_options=workflow_options,
        credential=azure_credential,
    )

    answer_generator: providers.Factory[AnswerGenerator] = providers.Factory(
        AnswerGenerator,
        settings=config,
        logger=logger,
        citation_tracker=citation_tracker,
        credential=azure_credential,
    )

    pii_detection_service: providers.Singleton[IPIIDetectionService] = providers.Singleton(
        PIIDetectionService,
        settings=config,
        logger=logger,
    )

    foundry_service: providers.Factory[IFoundryService] = providers.Factory(
        FoundryService,
        options=foundry_agent_options,
        logger=logger,
    )

    agentic_rag_workflow = providers.Factory(
        AgenticRAGWorkflow,
        settings=config,
        logger=logger,
        workflow_options=workflow_options,
        search_service=search_service,
        citation_tracker=citation_tracker,
        query_rewriter=query_rewriter,
        answer_generator=answer_generator,
        reflection_agent=reflection_agent,
        pii_detection_service=pii_detection_service,
        pii_detection_options=pii_detection_options,
    )

    chat_history_service: providers.Factory[IChatHistoryService] = providers.Factory(
        ChatHistoryService,
        repo=cosmos_repository,
        logger=logger,
    )

    chat_service: providers.Factory[IChatService] = providers.Factory(
        ChatService,
        logger=logger,
        workflow_options=workflow_options,
        chat_history_service=chat_history_service,
        workflow=agentic_rag_workflow,
        foundry_service=foundry_service,
        foundry_agent_options=foundry_agent_options,
        pii_detection_service=pii_detection_service,
        pii_detection_options=pii_detection_options,
    )

    return (
        citation_tracker,
        query_rewriter,
        reflection_agent,
        answer_generator,
        pii_detection_service,
        foundry_service,
        agentic_rag_workflow,
        chat_history_service,
        chat_service,
    )
