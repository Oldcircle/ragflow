"""sub_librarian — observe / summarize / write-notes subagent.

Phase 2.8 (v2.0.0): rewritten on the PromptSection model. Librarian-specific
rules (must persist with doc_create_note, single-note-per-turn limit,
note-title format) live as inline sections in this file; everything else
comes from the reflective preset.
"""

from __future__ import annotations

from ...prompting import PromptCtx, PromptSection, assemble_prompt, sections
from ...tools import _names as names
from ..schema import AgentDefinition


LIBRARIAN_TOOLS: list[str] = [
    # Observe
    names.KB_STATS,
    names.KB_AUDIT,
    names.DOC_LIST_RECENT_CHANGES,
    # Retrieve content to write about
    names.RAG_RETRIEVE,
    names.RAG_LIST_DOCS,
    names.RAG_READ_DOC,
    # Produce a durable artifact
    names.DOC_CREATE_NOTE,
    # Interactive
    names.ASK_USER_QUESTION,
    names.SUBMIT_PLAN,
]


_LIBRARIAN_MISSION = (
    "Observe the state of a KB, synthesize findings, and — when the user "
    "asks for a report / FAQ / summary — commit the result as a new "
    "document via doc_create_note. You produce understanding and "
    "writings, never state changes to existing documents."
)


_LIBRARIAN_DOMAIN_RULES = [
    # Persistence rule — librarian's defining constraint.
    "When the user asks for a 'report' / 'summary' / 'FAQ' / 'audit "
    "writeup' / 'notes', you MUST persist the output via doc_create_note. "
    "A chat-only summary that vanishes at end-of-turn is a failure mode — "
    "save it.",
    # Scope discipline — never overstep into ops territory.
    "You do NOT have tag / rename / archive / reparse / upload_from_url / "
    "kb_create. If the user's request requires a destructive operation, "
    "summarize what you'd recommend and tell the supervisor to delegate "
    "sub_archivist. Do not attempt the change yourself.",
    # Note format consistency — title / tags discipline so notes stay
    # discoverable.
    "Use note titles in the format 'YYYY-MM-DD · <topic>'. Auto-tag every "
    "note with at least ['agent_note', 'by:sub_librarian'] plus 1-2 "
    "topical tags.",
    # Per-turn rate limit — keeps the cost predictable.
    "A single turn produces at most ONE new note via doc_create_note. If "
    "the user's ask spans multiple topics, submit a plan listing the "
    "notes you propose to write and wait for approval before the batch.",
    # Anti-fabrication — librarian must trace every metric.
    "Never fabricate metrics. Every number in your output must come from "
    "a tool response (kb_stats / kb_audit / rag_retrieve / "
    "doc_list_recent_changes). If a metric is unavailable, say so.",
]


_LIBRARIAN_WORKFLOW_BODY = """\
# Workflow

1. Start every investigation with `kb_stats` — cheap (<1KB), anchors you
   in reality. Do NOT skip on the assumption that you remember the KB.
2. If the user asked 'how is this KB' / 'any issues' → call `kb_audit`
   with sensible stale_days (default 180). Use its `suggestions` field
   as section-heading outline.
3. If the user asked 'what changed recently' → call
   `doc_list_recent_changes` with a matching window.
4. If the user asked for full factual content on a specific topic →
   call `rag_retrieve` once or twice, then `rag_read_doc` on the most
   relevant doc(s).
5. Draft the Markdown body. Structure: `# Title` → `## Scope` →
   `## Findings` → `## Recommendations`. Every factual sentence ends
   with [N] tying it to the chunk it came from.
6. Call `doc_create_note(kb_id, title, markdown_body, tags)`. Confirm
   the response status is `ok` or `duplicate`. Report the new doc_id to
   the caller in one sentence."""


def _make_librarian_workflow_section() -> PromptSection:
    return PromptSection(
        name="librarian_workflow",
        compute=lambda _t, _c: _LIBRARIAN_WORKFLOW_BODY,
    )


def _build_librarian_system_prompt(_ctx: dict | None = None) -> str:
    """Materialize the librarian body via the Phase 2.8 section pipeline."""
    static = sections.reflective_static_sections(
        mission=_LIBRARIAN_MISSION,
        extra_constraints=_LIBRARIAN_DOMAIN_RULES,
    )
    # Slot the librarian-specific workflow into the preset, after the
    # standard hard constraints and before the tone / length / output
    # sections. This keeps the role / mission / read-only / hard
    # constraints as the cacheable head and adds a librarian-shaped
    # workflow on top.
    workflow = _make_librarian_workflow_section()
    insert_after = "strict_rag_constraints"
    out: list[PromptSection] = []
    for s in static:
        out.append(s)
        if s.name == insert_after:
            out.append(workflow)

    ctx = PromptCtx(
        role_line="You are sub_librarian, the knowledge base investigator-scribe.",
        enabled_tools=frozenset(LIBRARIAN_TOOLS),
    )
    return assemble_prompt(
        static_sections=out,
        dynamic_sections=sections.supervisor_dynamic_sections(),
        ctx=ctx,
    )


DEFINITION = AgentDefinition(
    name="sub_librarian",
    version="2.0.0",  # major bump — Phase 2.8 prompt rewrite
    description=(
        "KB investigator and scribe. Audits KB health, surveys recent "
        "changes, retrieves evidence, and writes a Markdown report back "
        "into the KB via doc_create_note. Never modifies existing "
        "documents — recommends, doesn't execute."
    ),
    when_to_use=(
        "User asks to understand / inspect / summarize a KB, or wants a "
        "durable note / FAQ / report written from retrieved content. "
        "Spawn sub_archivist separately if changes to existing documents "
        "are also needed."
    ),
    kind="subagent",
    icon="📚",
    category="ops",
    system_prompt=_build_librarian_system_prompt,
    model="inherit",
    max_turns=12,
    max_budget_usd=0.4,
    tools=LIBRARIAN_TOOLS,
    citation_enforce="warn",
    citation_numeric_strict=True,
    can_spawn_subagents=False,
    history_turn_limit=6,
)
