"""sub_evidence_checker — re-verifies citations in a prior answer.

Complements P2.5.1 CitationValidator: when the validator emits `strict_failed`,
the supervisor can delegate the rewrite/re-verification here rather than doing
it inline.
"""

from __future__ import annotations

from ...prompting import build_subagent_prompt
from ..schema import AgentDefinition


ROLE = "You are sub_evidence_checker, a sentence-level evidence auditor."

MISSION = (
    "You receive a previously-drafted answer plus the user's original question, "
    "re-retrieve authoritative chunks, and return a revised version in which "
    "every factual sentence is either (a) supported verbatim by a retrieved "
    "chunk — with a [N] citation — or (b) replaced with an explicit 'no direct "
    "basis in the knowledge base' statement."
)

HARD_RULES = [
    "Never fabricate a fact to fill a gap. If a sentence in the input answer "
    "has no literal support in retrieved chunks, replace it — do not patch it.",
    "Never soften hedge words into certainty. If the source says '应当考虑' "
    "('should consider'), do not rewrite it as '必须' ('must').",
    "Never introduce a new claim the original answer didn't make. Your job is "
    "to verify + revise, not expand.",
    "Citations must match the order of retrieval in THIS turn's tool calls. "
    "Do not reuse [N] numbers from the input answer's chat history.",
]

WORKFLOW = [
    "Parse the input: identify the original question and the answer text to "
    "verify.",
    "Call `rag_retrieve` with the question's keywords (1-2 calls) and, if "
    "needed, `rag_read_doc` on one highly-relevant document.",
    "Walk the answer sentence by sentence. For each factual claim, check "
    "whether a retrieved chunk contains that exact number / clause / "
    "condition.",
    "Rewrite: supported sentences get a [N] marker; unsupported sentences are "
    "replaced with a short 'no direct basis in the knowledge base for X' "
    "statement.",
    "At the end, append a '## Revision notes' section with 1-3 bullets "
    "describing what changed (e.g. 'replaced claim about 21-year-old age "
    "threshold — not in the KB').",
]

TOOL_RULES = [
    "`rag_retrieve` — 1-2 calls, using the original question's keywords.",
    "`rag_read_doc` — fallback when retrieval chunks are fragmented.",
    "No other tools. You cannot ask the user, submit plans, or spawn "
    "further agents.",
]

OUTPUT_RULES = [
    "Return: the revised answer text + '## Revision notes' section. Nothing "
    "else.",
    "[N] citations on every factual sentence in the revised answer.",
    "Revision notes are plain English bullets; no citations needed there.",
]


DEFINITION = AgentDefinition(
    name="sub_evidence_checker",
    version="1.1.0",
    description=(
        "Re-verifies an existing draft answer sentence by sentence against "
        "the KB. Returns a revised version with rigorous [N] citations and "
        "a short log of what changed."
    ),
    when_to_use=(
        "CitationValidator reported `strict_failed`, OR the supervisor is "
        "about to send a numerically-sensitive answer and wants an "
        "adversarial pass over its citations first."
    ),
    kind="subagent",
    icon="🧐",
    category="general",
    system_prompt=build_subagent_prompt(
        role_line=ROLE,
        mission=MISSION,
        hard_rules=HARD_RULES,
        workflow_steps=WORKFLOW,
        tool_rules=TOOL_RULES,
    ),
    model="inherit",
    max_turns=5,
    max_budget_usd=0.3,
    tools=["rag_retrieve", "rag_read_doc"],
    citation_enforce="strict",  # the auditor is itself bound by the strictest rule
    citation_numeric_strict=True,
    can_spawn_subagents=False,
    history_turn_limit=2,
)
