"""Legal / compliance advisor — supervisor agent."""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="legal-contract",
    version="1.0.0",
    description=(
        "For in-house legal and compliance teams. Answers clause-level "
        "questions grounded in a company-private KB of contract templates, "
        "regulatory rule sets, and historical case precedents."
    ),
    when_to_use=(
        "User asks about contract clauses, compliance obligations, or "
        "interpretation of regulations that live in this company's KB."
    ),
    kind="supervisor",
    icon="⚖️",
    category="legal",
    system_prompt=strict_rag_prompt(
        role="a legal and compliance advisor",
        fallback="escalate to the legal team for definitive review",
        extras=[
            "Do not render conclusive legal judgments. Prefer phrasing like "
            "'Based on clause X, the recommended position is Y' rather than "
            "'this is legal / illegal'. The user is responsible for the final "
            "call.",
            "When multiple clauses could apply, enumerate them; never pick "
            "one silently. Cite each with [N] so the reader can verify.",
        ],
    ),
    max_turns=12,
    max_budget_usd=0.8,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("contract", "legal", "compliance", "regulation", "合同", "法律", "合规"),
    citation_enforce="warn",
    citation_numeric_strict=True,
    can_spawn_subagents=True,
    allowed_subagent_types=(
        "sub_policy_researcher",
        "sub_evidence_checker",
        "sub_archivist",
        "sub_librarian",
    ),
)
