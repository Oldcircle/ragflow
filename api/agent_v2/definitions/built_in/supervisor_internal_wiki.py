"""Internal wiki / SOP advisor — supervisor agent."""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="internal-wiki",
    version="2.0.0",
    description=(
        "General-purpose internal knowledge advisor. Point it at your company "
        "handbook, SOP, process, and training KBs so employees can ask the "
        "agent directly instead of grepping intranet pages."
    ),
    when_to_use=(
        "Employees asking about company policies, SOPs, onboarding, HR, IT "
        "procedures, or training materials that live in the internal KB."
    ),
    kind="supervisor",
    icon="📚",
    category="general",
    system_prompt=strict_rag_prompt(
        role="an internal company knowledge assistant",
        fallback=(
            "search your company's ticket system, or contact the relevant "
            "IT / HR / admin point of contact"
        ),
    ),
    max_turns=8,
    max_budget_usd=0.5,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("SOP", "handbook", "wiki", "process", "manual"),
    citation_enforce="warn",
    can_spawn_subagents=False,
)
