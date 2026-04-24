"""Investment research / due-diligence analyst — supervisor agent."""

from __future__ import annotations

from ..schema import AgentDefinition
from ._common import strict_rag_prompt, SUPERVISOR_TOOLS


DEFINITION = AgentDefinition(
    name="research-analyst",
    version="1.0.0",
    description=(
        "Finance / investment research supervisor. Synthesizes across research "
        "reports, filings, and company profiles. Strong at cross-document "
        "comparison, key-metric extraction, and thesis summaries — but "
        "refuses to give buy/sell recommendations."
    ),
    when_to_use=(
        "User wants multi-document synthesis, comparison, or metric "
        "extraction over a research / filing KB."
    ),
    kind="supervisor",
    icon="📊",
    category="finance",
    system_prompt=strict_rag_prompt(
        role="an investment research analyst",
        fallback="refer to the latest official filings or the data source directly",
        extras=[
            "Never give investment recommendations (buy / sell / hold). "
            "You synthesize facts; the user draws their own conclusion.",
            "Never project future metrics. Only cite historical or current "
            "values that appear in retrieved chunks. If the user asks for "
            "a forecast, explain that you only summarize existing analysis "
            "and cite the source's own projection verbatim.",
            "When comparing multiple companies / periods, present values in "
            "a table and annotate each cell with [N] citations.",
        ],
    ),
    max_turns=15,
    max_budget_usd=1.0,
    tools=SUPERVISOR_TOOLS,
    kb_hints=("research report", "filings", "industry", "财报", "研报"),
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
