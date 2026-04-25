"""sub_evidence_checker — re-verifies citations in a prior answer.

Phase 2.8 (v2.0.0): rewritten on the PromptSection model. Domain rules
(no fabrication / no hedge softening / no new claims / fresh [N] numbering)
live as ``extra_constraints`` on the reflective preset. A small workflow
section is added inline.

Complements P2.5.1 CitationValidator: when the validator emits
``strict_failed``, the supervisor can delegate rewrite / re-verification
here instead of running it inline.
"""

from __future__ import annotations

from ...prompting import PromptCtx, PromptSection, assemble_prompt, sections
from ...tools import _names as names
from ..schema import AgentDefinition


CHECKER_TOOLS: list[str] = [names.RAG_RETRIEVE, names.RAG_READ_DOC]


_CHECKER_MISSION = (
    "You receive a previously-drafted answer plus the user's original "
    "question, re-retrieve authoritative chunks, and return a revised "
    "version in which every factual sentence is either (a) supported "
    "verbatim by a retrieved chunk — with a [N] citation — or (b) "
    "replaced with an explicit 'no direct basis in the knowledge base' "
    "statement."
)


_CHECKER_DOMAIN_RULES = [
    "Never fabricate a fact to fill a gap. If a sentence in the input "
    "answer has no literal support in retrieved chunks, REPLACE it — do "
    "not patch it.",
    "Never soften hedge words into certainty. If the source says "
    "'应当考虑' ('should consider'), do not rewrite as '必须' ('must').",
    "Never introduce a new claim the original answer didn't make. Your "
    "job is to verify + revise, not expand.",
    "Citations must match the order of retrieval in THIS turn's tool "
    "calls. Do not reuse [N] numbers from the input answer's chat "
    "history.",
]


_CHECKER_WORKFLOW_BODY = """\
# Workflow

1. Parse the input: identify the original question and the answer text
   to verify.
2. Call `rag_retrieve` with the question's keywords (1-2 calls) and, if
   needed, `rag_read_doc` on one highly-relevant document.
3. Walk the answer sentence by sentence. For each factual claim, check
   whether a retrieved chunk contains that exact number / clause /
   condition.
4. Rewrite: supported sentences get a [N] marker; unsupported sentences
   are replaced with 'no direct basis in the knowledge base for X'.
5. At the end, append a '## Revision notes' section with 1-3 bullets
   describing what changed."""


def _make_checker_workflow_section() -> PromptSection:
    return PromptSection(
        name="checker_workflow",
        compute=lambda _t, _c: _CHECKER_WORKFLOW_BODY,
    )


def _build_checker_system_prompt(_ctx: dict | None = None) -> str:
    static = sections.reflective_static_sections(
        mission=_CHECKER_MISSION,
        extra_constraints=_CHECKER_DOMAIN_RULES,
    )
    workflow = _make_checker_workflow_section()
    out: list[PromptSection] = []
    for s in static:
        out.append(s)
        if s.name == "strict_rag_constraints":
            out.append(workflow)

    ctx = PromptCtx(
        role_line="You are sub_evidence_checker, a sentence-level evidence auditor.",
        enabled_tools=frozenset(CHECKER_TOOLS),
    )
    return assemble_prompt(
        static_sections=out,
        dynamic_sections=sections.supervisor_dynamic_sections(),
        ctx=ctx,
    )


DEFINITION = AgentDefinition(
    name="sub_evidence_checker",
    version="2.0.0",  # major bump — Phase 2.8 prompt rewrite
    description=(
        "Re-verifies an existing draft answer sentence by sentence "
        "against the KB. Returns a revised version with rigorous [N] "
        "citations and a short log of what changed."
    ),
    when_to_use=(
        "CitationValidator reported `strict_failed`, OR the supervisor "
        "is about to send a numerically-sensitive answer and wants an "
        "adversarial pass over its citations first."
    ),
    kind="subagent",
    icon="🧐",
    category="general",
    system_prompt=_build_checker_system_prompt,
    model="inherit",
    max_turns=5,
    max_budget_usd=0.3,
    tools=CHECKER_TOOLS,
    citation_enforce="strict",  # the auditor is itself bound by the strictest rule
    citation_numeric_strict=True,
    can_spawn_subagents=False,
    history_turn_limit=2,
)
