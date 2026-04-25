"""sub_policy_researcher — deep-dive on a single policy.

Phase 2.8 (v2.0.0): rewritten on the PromptSection model. Domain rules
(stay on one policy / answer only what was asked / verbatim quote +
[N] / never fabricate clause numbers) live as ``extra_constraints`` on
the reflective preset; a small workflow section is added inline.
"""

from __future__ import annotations

from ...prompting import PromptCtx, PromptSection, assemble_prompt, sections
from ...tools import _names as names
from ..schema import AgentDefinition


RESEARCHER_TOOLS: list[str] = [names.RAG_RETRIEVE, names.RAG_READ_DOC]


_RESEARCHER_MISSION = (
    "When the supervisor already knows which policy document to study, "
    "trace a specific question through that document — finding exact "
    "clause wording, comparing revisions, resolving ambiguity — and "
    "return verbatim text with [N] citations. You do NOT synthesize or "
    "recommend; you surface what the policy literally says."
)


_RESEARCHER_DOMAIN_RULES = [
    "Stay on the one policy the supervisor named. Do not drift into "
    "adjacent policies, even if retrieval surfaces them.",
    "Answer only the question asked. Do not volunteer interpretation, "
    "comparison, or 'next steps' beyond the narrow question.",
    "Never fabricate clause numbers or section names. If retrieval "
    "fails to find the exact clause, reply 'This policy does not "
    "contain a relevant clause for the question' — do not guess.",
    "Every factual sentence ends with [N]. No exceptions.",
]


_RESEARCHER_WORKFLOW_BODY = """\
# Workflow

1. Read the supervisor's brief: identify the policy name and the exact
   question to answer.
2. Call `rag_retrieve` 2-3 times with policy-name + question keywords.
   Vary keywords across calls; stop when you have the relevant chunks
   or 3 attempts failed.
3. If retrieval yields chunks but they don't directly answer, call
   `rag_read_doc` on the full document once and scan for the relevant
   section.
4. Compose the answer: quote the relevant clause verbatim (in its
   original language), annotate with [N], and add one sentence of
   restatement only if the clause is hard to read."""


def _make_researcher_workflow_section() -> PromptSection:
    return PromptSection(
        name="researcher_workflow",
        compute=lambda _t, _c: _RESEARCHER_WORKFLOW_BODY,
    )


def _build_researcher_system_prompt(_ctx: dict | None = None) -> str:
    static = sections.reflective_static_sections(
        mission=_RESEARCHER_MISSION,
        extra_constraints=_RESEARCHER_DOMAIN_RULES,
    )
    workflow = _make_researcher_workflow_section()
    out: list[PromptSection] = []
    for s in static:
        out.append(s)
        if s.name == "strict_rag_constraints":
            out.append(workflow)

    ctx = PromptCtx(
        role_line="You are sub_policy_researcher, a focused reader of one specific policy.",
        enabled_tools=frozenset(RESEARCHER_TOOLS),
    )
    return assemble_prompt(
        static_sections=out,
        dynamic_sections=sections.supervisor_dynamic_sections(),
        ctx=ctx,
    )


DEFINITION = AgentDefinition(
    name="sub_policy_researcher",
    version="2.0.0",  # major bump — Phase 2.8 prompt rewrite
    description=(
        "Deep-reads a single named policy to answer one specific "
        "tracing question with verbatim clause quotes + [N] citations. "
        "Spawns no further agents."
    ),
    when_to_use=(
        "Supervisor has already identified the exact policy document "
        "and needs close reading on one clause — wording, version "
        "delta, anti-arbitrage restriction, etc. Not for discovery "
        "across documents."
    ),
    kind="subagent",
    icon="🔎",
    category="policy",
    system_prompt=_build_researcher_system_prompt,
    model="inherit",
    max_turns=6,
    max_budget_usd=0.3,
    tools=RESEARCHER_TOOLS,
    citation_enforce="warn",
    citation_numeric_strict=True,
    can_spawn_subagents=False,
    history_turn_limit=4,
)
