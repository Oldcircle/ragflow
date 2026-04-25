"""Shared definitions for built-in agents.

Phase 2.8: ``strict_rag_prompt`` now returns a callable that materializes
the supervisor body via the PromptSection pipeline (see ``prompting/sections.py``),
rather than the legacy single-string ``build_supervisor_prompt`` template.
Domain specialization is preserved via the ``role`` / ``fallback`` /
``extras`` parameters; existing supervisor files stay unchanged.

This module only deals with text assembly. Runtime context (kb_ids,
tenant) is injected by the runner / spawn flow, not at definition time.
"""

from __future__ import annotations

from collections.abc import Callable

from ...prompting import PromptCtx, assemble_prompt, sections
from ...tools import _names as names


# Phase 2.6 v0.2 design constraint: supervisor only does "retrieval QA +
# delegation", no direct write / audit tools. Writes go through
# sub_archivist, audits / notes go through sub_librarian. Keeps the
# architectural separation from being bypassed by a loose ``tools="*"``
# setting.
SUPERVISOR_TOOLS = [
    # Read
    names.RAG_RETRIEVE,
    names.RAG_LIST_DOCS,
    names.RAG_READ_DOC,
    names.RAG_GRAPH_QUERY,
    # Cheap health snapshot (<1KB, no side effects)
    names.KB_STATS,
    # Delegation + user interaction
    names.SPAWN_SUBAGENT,
    names.ASK_USER_QUESTION,
    names.SUBMIT_PLAN,
]


def strict_rag_prompt(
    *,
    role: str,
    fallback: str,
    extras: list[str] | None = None,
) -> Callable[[dict | None], str]:
    """Phase 2.8 supervisor prompt builder — returns a callable.

    ``role`` is in English (e.g. "the Shenzhen Affordable Housing Policy
    Advisor"). ``fallback`` is the phrase the agent emits when the KB
    does not cover a fact (e.g. "consult your local housing bureau").
    ``extras`` are additional domain-specific hard constraints.

    The returned callable matches ``schema.SystemPromptFn`` so it plugs
    into ``AgentDefinition.system_prompt`` directly.

    Body composition (via ``sections.supervisor_static_sections``):
        identity → domain_context → strict_rag_constraints (with
        domain extras) → workflow → delegation → clarify_vs_act →
        actions_risk → tone_and_style → numeric_length_anchors →
        retrieval_output_rules
    """
    domain_context = (
        f"You operate on a knowledge base curated for {role}. "
        f"If users ask questions outside this scope, reply briefly that "
        f"you can only answer questions relating to {role}, and do not "
        f"call any tools."
    )
    fallback_constraint = (
        "When the knowledge base does not directly answer the user, say "
        f"exactly: 'No direct basis in the knowledge base; please "
        f"{fallback}.' Do not bridge the gap with training knowledge."
    )
    constraints: list[str] = [fallback_constraint]
    if extras:
        constraints.extend(extras)

    role_line = f"You are {role}, answering strictly from the knowledge base."

    def _build(_ctx: dict | None = None) -> str:
        static = sections.supervisor_static_sections(
            extra_constraints=constraints
        )
        ctx = PromptCtx(
            role_line=role_line,
            enabled_tools=frozenset(SUPERVISOR_TOOLS),
            domain_context=domain_context,
        )
        return assemble_prompt(
            static_sections=static,
            dynamic_sections=sections.supervisor_dynamic_sections(),
            ctx=ctx,
        )

    return _build
