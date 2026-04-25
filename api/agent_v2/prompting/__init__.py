"""Prompt construction helpers.

Phase 2.8 introduced a ``PromptSection``-based architecture (see
``PLAN-prompt-architecture.md``); ``build_supervisor_prompt`` /
``build_subagent_prompt`` are now thin shims over ``assemble_prompt`` for
backward compat with existing AgentDefinition files.

Public entry points:
    build_supervisor_prompt(role_line, ...)        — legacy, still works
    build_subagent_prompt(role_line, ...)          — legacy, still works
    build_tool_description(when, what, ...)
    PromptSection / PromptCtx / PromptCache         — Phase 2.8 model
    assemble_prompt(static, dynamic, ctx)           — Phase 2.8 entry
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY                  — boundary marker
    SEARCH_HINT_BY_TOOL / STRICT_RAG_CONSTRAINTS / RETRIEVAL_OUTPUT_RULES
"""

from .builder import (  # noqa: F401
    RETRIEVAL_OUTPUT_RULES,
    SEARCH_HINT_BY_TOOL,
    STRICT_RAG_CONSTRAINTS,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    PromptCache,
    PromptCtx,
    PromptSection,
    assemble_prompt,
    build_subagent_prompt,
    build_supervisor_prompt,
    build_tool_description,
    prepend_bullets,
    render_tool_availability_section,
)
from . import sections  # noqa: F401

__all__ = [
    "RETRIEVAL_OUTPUT_RULES",
    "SEARCH_HINT_BY_TOOL",
    "STRICT_RAG_CONSTRAINTS",
    "SYSTEM_PROMPT_DYNAMIC_BOUNDARY",
    "PromptCache",
    "PromptCtx",
    "PromptSection",
    "assemble_prompt",
    "build_subagent_prompt",
    "build_supervisor_prompt",
    "build_tool_description",
    "prepend_bullets",
    "render_tool_availability_section",
    "sections",
]
