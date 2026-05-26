"""Prompt templates for Azure AI Search skillsets and AI services."""

from app.prompts.templates import (
    RAG_ASSISTANT_SYSTEM_PROMPT,
    AnswerGeneratorPrompts,
    IngestionPrompts,
    QueryRewriterPrompts,
    ReflectionAgentPrompts,
)

__all__ = [
    "AnswerGeneratorPrompts",
    "ReflectionAgentPrompts",
    "IngestionPrompts",
    "QueryRewriterPrompts",
    "RAG_ASSISTANT_SYSTEM_PROMPT",
]
