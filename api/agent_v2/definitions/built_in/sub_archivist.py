"""sub_archivist — destructive-ops subagent.

Phase 2.8 (v2.0.0): rewritten on the PromptSection model. Operator-flavored
prompt is assembled from shared sections; previous 192-line definition with
inline ARCHIVIST_HARD_RULES / WORKFLOW / TOOL_RULES / OUTPUT_RULES lists is
gone. Behavioral contract is unchanged: same plan_gate semantics, same
[step K/N done] markers, same hard refusal on ambiguous brief.
"""

from __future__ import annotations

from ...prompting import PromptCtx, assemble_prompt, sections
from ...tools import _names as names
from ..schema import AgentDefinition


# Operator subagent — owns destructive write tools + verification reads +
# interactive (ask / submit_plan) + plan-readback (get_pending_plan).
ARCHIVIST_TOOLS: list[str] = [
    # Write — destructive / state-changing
    names.DOC_TAG,
    names.DOC_RENAME,
    names.DOC_ARCHIVE,
    names.DOC_REPARSE,
    names.DOC_UPLOAD_FROM_URL,
    names.KB_CREATE,
    # Phase 2.7 — attachment archive flow
    names.WEB_FETCH_TO_ATTACHMENT,
    names.DOC_INGEST_ATTACHMENT,
    # Read — verification only
    names.RAG_LIST_DOCS,
    names.RAG_READ_DOC,
    # Interactive
    names.ASK_USER_QUESTION,
    names.SUBMIT_PLAN,
    # Phase 2.6 v0.6 — plan execution loop
    names.GET_PENDING_PLAN,
]


_ARCHIVIST_MISSION = (
    "Execute **destructive or state-changing** KB operations — tagging, "
    "renaming, archiving, re-parsing, ingesting from a URL, creating a "
    "new KB — on behalf of a supervisor that has already confirmed the "
    "user's intent. You are an operator: you produce state changes plus "
    "one-line confirmations, not analytical reports."
)


def _build_archivist_system_prompt(_ctx: dict | None = None) -> str:
    """Materialize the archivist body via the Phase 2.8 section pipeline."""
    static = sections.operator_static_sections(mission=_ARCHIVIST_MISSION)
    ctx = PromptCtx(
        role_line="You are sub_archivist, the knowledge base operations specialist.",
        enabled_tools=frozenset(ARCHIVIST_TOOLS),
    )
    return assemble_prompt(
        static_sections=static,
        dynamic_sections=sections.supervisor_dynamic_sections(),
        ctx=ctx,
    )


DEFINITION = AgentDefinition(
    name="sub_archivist",
    version="2.0.0",  # major bump — Phase 2.8 prompt rewrite
    description=(
        "Knowledge base operations specialist. Tags, renames, archives, "
        "reparses, uploads from URL, creates KBs, ingests staged "
        "attachments — on explicit user request only. Refuses ambiguous "
        "briefs; uses submit_plan + plan_gate for any batch."
    ),
    when_to_use=(
        "Supervisor needs a state-changing operation on KB content: "
        "tagging, cross-KB archiving, renaming, re-parsing, URL ingest, "
        "or a new KB. Always requires the user to have explicitly asked "
        "for the change."
    ),
    kind="subagent",
    icon="📦",
    category="ops",
    system_prompt=_build_archivist_system_prompt,
    model="inherit",
    max_turns=15,
    max_budget_usd=0.4,
    tools=ARCHIVIST_TOOLS,
    citation_enforce="off",  # operators don't produce [N] citations
    citation_numeric_strict=False,
    can_spawn_subagents=False,
    history_turn_limit=6,
)
