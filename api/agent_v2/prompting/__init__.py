"""Prompt construction helpers (Phase 2.6 v0.3).

Inspired by Claude Code's 8-section system-prompt convention (see
`/tmp/architectural-synthesis.md` §5 and `AUDIT-claude-code-alignment.md`).

Public entry points:
    build_supervisor_prompt(role, domain_rules, tool_usage_rules, delegation_rules)
    build_subagent_prompt(role, mission, hard_rules, tools_to_use, output_format)
    build_tool_description(when, what, usage_notes, examples, related)
    SEARCH_HINT_BY_TOOL

The `build_*_prompt` helpers produce **English** prompts that agents can
rely on as stable, section-indexed instructions. We're not porting Claude
Code's `enhanceSystemPromptWithEnvDetails` or the cache-boundary marker
(not useful for DeepSeek); just the structural convention.
"""

from .builder import (  # noqa: F401
    RETRIEVAL_OUTPUT_RULES,
    SEARCH_HINT_BY_TOOL,
    STRICT_RAG_CONSTRAINTS,
    build_subagent_prompt,
    build_supervisor_prompt,
    build_tool_description,
    render_tool_availability_section,
)

__all__ = [
    "RETRIEVAL_OUTPUT_RULES",
    "SEARCH_HINT_BY_TOOL",
    "STRICT_RAG_CONSTRAINTS",
    "build_subagent_prompt",
    "build_supervisor_prompt",
    "build_tool_description",
    "render_tool_availability_section",
]
