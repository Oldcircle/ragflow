"""Shared PromptSection library — Phase 2.8.

Each ``make_*_section()`` factory returns a ``PromptSection`` ready to plug
into ``assemble_prompt(static_sections=..., dynamic_sections=...)``. A
section returns ``None`` from its ``compute`` callback to drop itself
entirely from the rendered prompt — the standard claude-code-ref pattern
(``src/constants/prompts.ts:495-559``).

## Design conventions

1. **Section names are stable strings**. They double as cache keys; renaming
   one busts the cache for that turn. Use snake_case, descriptive.
2. **``compute(enabled_tools, ctx)`` is the only signature**. Implementations
   may ignore either arg, but the signature is non-negotiable so cache and
   resolve flow stay uniform.
3. **Most sections are static** (``cache_break=False``). Use cache_break only
   when the rendered text genuinely changes per turn (current
   ``pending_plan_status``, current attachment list, etc.).
4. **Bullets through ``prepend_bullets``**. Hand-rolling ``- foo`` strings is
   tolerated for one-shot lines but discouraged in multi-bullet sections.
5. **Tool names through ``_names``**. Bare ``"submit_plan"`` literals are
   forbidden in this module (D3 fix).

## Section catalog

Static sections (cacheable):
    make_identity_section            — # Role
    make_mission_section             — # Mission
    make_actions_risk_section        — # Executing actions with care
    make_using_your_tools_section    — # Using your tools (delegated to render_tool_availability_section)
    make_tone_and_style_section      — # Tone and style
    make_numeric_length_anchors      — # Length limits
    make_strict_rag_constraints      — # Hard constraints (RAG)
    make_retrieval_output_rules      — # Output format ([N] citations)
    make_read_only_block             — === CRITICAL: READ-ONLY MODE ===
    make_plan_gate_block             — # Plan gate (runtime-enforced)
    make_supervisor_workflow         — # Workflow (supervisor delegation tree)
    make_supervisor_delegation_rules — # Delegation rules
    make_clarify_vs_act              — # Clarify-vs-act decision tree
    make_no_citation_for_operators   — # Output format (operator subagents)
    make_step_marker_rules           — # Output format ([step K/N done] markers)

Dynamic sections (cache_break):
    make_pending_plan_section        — # Pending plan (current status)
    make_kb_scope_section            — # KB scope (active kb_ids)

References:
- ``vendor/claude-code-ref/packages/builtin-tools/src/tools/AgentTool/built-in/exploreAgent.ts:25-37`` — READ-ONLY block model
- ``vendor/claude-code-ref/src/constants/prompts.ts:271-316`` — Using your tools
- ``vendor/claude-code-ref/src/constants/prompts.ts:434-446`` — Tone and style
- ``vendor/claude-code-ref/src/constants/prompts.ts:535-541`` — Length limits
- ``vendor/claude-code-ref/src/constants/prompts.ts:257-269`` — Actions / risk
"""

from __future__ import annotations

from collections.abc import Iterable

from ..tools import _names as names
from .builder import (
    RETRIEVAL_OUTPUT_RULES,
    STRICT_RAG_CONSTRAINTS,
    PromptCtx,
    PromptSection,
    prepend_bullets,
    render_tool_availability_section,
)


# ────────────────────────────  Identity / mission  ────────────────────────────


def make_identity_section() -> PromptSection:
    """``# Role`` — single-sentence agent identity from ``ctx.role_line``."""

    def _compute(_tools: frozenset[str], ctx: PromptCtx) -> str | None:
        if not ctx.role_line.strip():
            return None
        return f"# Role\n\n{ctx.role_line.strip()}"

    return PromptSection(name="identity", compute=_compute)


def make_mission_section(mission: str) -> PromptSection:
    """``# Mission`` — what this agent is FOR. One paragraph."""
    text = mission.strip()

    def _compute(_tools: frozenset[str], _ctx: PromptCtx) -> str | None:
        return f"# Mission\n\n{text}" if text else None

    return PromptSection(name="mission", compute=_compute)


def make_domain_context_section() -> PromptSection:
    """``# Domain context`` — pulled from ``ctx.domain_context``."""

    def _compute(_tools: frozenset[str], ctx: PromptCtx) -> str | None:
        ctx_text = ctx.domain_context.strip()
        if not ctx_text:
            return None
        return f"# Domain context\n\n{ctx_text}"

    return PromptSection(name="domain_context", compute=_compute)


# ────────────────────────────  Hard constraints  ──────────────────────────────


def make_strict_rag_constraints(extras: Iterable[str] | None = None) -> PromptSection:
    """``# Hard constraints (must / must-not)`` — STRICT_RAG_CONSTRAINTS + per-agent extras.

    Used by retrieval-flavored agents (supervisors, evidence_checker,
    policy_researcher). Operator agents that don't produce [N] citations
    should NOT include this section.
    """
    extra_list = list(extras or [])

    def _compute(_tools: frozenset[str], _ctx: PromptCtx) -> str | None:
        all_constraints = list(STRICT_RAG_CONSTRAINTS) + extra_list
        body = "\n".join(prepend_bullets(all_constraints))
        return f"# Hard constraints (must / must-not)\n\n{body}"

    return PromptSection(name="strict_rag_constraints", compute=_compute)


# ────────────────────────────  Read-only block  ───────────────────────────────


_READ_ONLY_BLOCK_BODY = """\
=== CRITICAL: READ-ONLY MODE ===

This is a READ-ONLY task. You are STRICTLY PROHIBITED from:
- Calling any KB-mutating tool (doc_tag, doc_rename, doc_archive,
  doc_reparse, doc_upload_from_url, kb_create, doc_create_note,
  doc_ingest_attachment, web_fetch_to_attachment)
- Spawning further subagents (you cannot delegate)
- Submitting a plan (only the supervisor or sub_archivist can plan)

Your role is EXCLUSIVELY to read, analyze, and report. Attempting to call
a write tool will fail at the runtime gate and surface as a policy
violation in the audit log."""


def make_read_only_block() -> PromptSection:
    """``=== CRITICAL: READ-ONLY MODE ===`` — used by sub_evidence_checker /
    sub_policy_researcher / sub_librarian (reflective subagents).

    Mirrors ``built-in/exploreAgent.ts:25-37`` and ``planAgent.ts:23-34``.
    Static — same text every render.
    """
    return PromptSection(
        name="read_only_block",
        compute=lambda _tools, _ctx: _READ_ONLY_BLOCK_BODY,
    )


# ──────────────────────────────  Plan gate  ───────────────────────────────────


_PLAN_GATE_BODY = """\
# Plan gate (runtime-enforced)

Before any batch of ≥3 state-changing operations, OR any cross-KB move,
OR any external URL ingest, you MUST call `submit_plan` first and STOP.

Two-layer enforcement (you cannot bypass):
- Same-turn lock: the runtime rejects write tools after `submit_plan` runs
  in the same turn.
- Cross-turn DB state: writes are rejected while `pending_plan_status` is
  `waiting` / `rejected` / `request_changes`. The gate lifts only when the
  next user message starts with `[plan approved]`.

After `submit_plan`, end the turn with one short line: 'Plan submitted for your review.' Do not re-narrate the plan in chat — the frontend renders the plan card from the SSE event."""


def make_plan_gate_block() -> PromptSection:
    """``# Plan gate`` — used by sub_archivist + supervisors that can submit
    plans. Section returns ``None`` if ``submit_plan`` is not enabled
    (D2 fix — section disappears when tool unavailable)."""

    def _compute(tools: frozenset[str], _ctx: PromptCtx) -> str | None:
        if names.SUBMIT_PLAN not in tools:
            return None
        return _PLAN_GATE_BODY

    return PromptSection(name="plan_gate_block", compute=_compute)


# ────────────────────────  Citation / output rules  ───────────────────────────


def make_retrieval_output_rules() -> PromptSection:
    """``# Output format`` — [N] citation rules (STRICT_RAG agents)."""

    def _compute(_tools: frozenset[str], _ctx: PromptCtx) -> str | None:
        body = "\n".join(prepend_bullets(RETRIEVAL_OUTPUT_RULES))
        return f"# Output format\n\n{body}"

    return PromptSection(name="retrieval_output_rules", compute=_compute)


_NO_CITATION_BODY = """\
# Output format

- Never produce [N] citations. You are an operator, not a writer.
- One sentence per operation: what you did + what changed + the new
  doc_id / kb_id.
- Do not add analytical commentary. Do not suggest further work unless
  asked."""


def make_no_citation_for_operators() -> PromptSection:
    """``# Output format`` — operator subagents (sub_archivist) variant.

    Used when ``citation_enforce='off'``. Replaces ``make_retrieval_output_rules``.
    """
    return PromptSection(
        name="no_citation_for_operators",
        compute=lambda _t, _c: _NO_CITATION_BODY,
    )


_STEP_MARKER_BODY = """\
# Step markers (operator workflow)

After every operation in a multi-step plan, emit:
- `[step K/N done: <verb> <resource>]` — K is 1-based step index, N is
  total step count from `get_pending_plan`.
- On failure: `[step K/N FAILED: <reason>]` — and STOP the batch. Do not
  silently skip to the next step.
- After the batch, one summary line: 'N succeeded, M failed' with one
  reason line per failure if M > 0.

There is no background wake-up / scheduled self-resume in this HTTP/SSE architecture. When async work was queued (doc_reparse, doc_upload_from_url), end with: 'Queued. Send "check progress" in your next message and I will verify it then.' Never imply automatic scheduled follow-up."""


def make_step_marker_rules() -> PromptSection:
    """``# Step markers`` — operator subagents that follow get_pending_plan."""

    def _compute(tools: frozenset[str], _ctx: PromptCtx) -> str | None:
        # Only surface this section when the agent can actually read back
        # an approved plan. If get_pending_plan isn't reachable, step
        # markers don't apply.
        if names.GET_PENDING_PLAN not in tools:
            return None
        return _STEP_MARKER_BODY

    return PromptSection(name="step_marker_rules", compute=_compute)


# ──────────────────────────  Tone, length, actions  ───────────────────────────


_TONE_AND_STYLE_BODY = """\
# Tone and style

- Only use emojis if the user explicitly requests them. Avoid them
  otherwise.
- Keep responses short and concise.
- When citing a chunk, use the [N] markers — the frontend renders the
  Sources panel from chunk ids automatically. Do not inline file names.
- Do not use a colon before tool calls. Text like "Let me search:" then a
  tool call should just be "Let me search." with a period."""


def make_tone_and_style_section() -> PromptSection:
    """``# Tone and style`` — mirrors claude-code-ref/prompts.ts:434-446."""
    return PromptSection(
        name="tone_and_style",
        compute=lambda _t, _c: _TONE_AND_STYLE_BODY,
    )


_NUMERIC_LENGTH_BODY = """\
# Length limits

- Keep text between tool calls to ≤25 words.
- Keep final responses to ≤100 words unless the task requires more
  detail (multi-step audit, complex policy explanation, etc.).
- For [step K/N done] markers, keep each line ≤30 words."""


def make_numeric_length_anchors() -> PromptSection:
    """``# Length limits`` — mirrors claude-code-ref/prompts.ts:535-541.

    Numeric anchors beat qualitative 'be concise' (D6 fix). Apply uniformly
    to supervisors and subagents."""
    return PromptSection(
        name="numeric_length_anchors",
        compute=lambda _t, _c: _NUMERIC_LENGTH_BODY,
    )


_ACTIONS_RISK_BODY = """\
# Executing actions with care

Carefully consider the reversibility and blast radius of each action.
You can freely take local, reversible reads. For actions that mutate KB
state, cross knowledge bases, or pull external content, the cost of
pausing to confirm is low while the cost of an unwanted action (lost
documents, mis-archived files, leaked URLs) can be high.

When in doubt, submit a plan or ask the user. Approval for one action
does not extend to other actions; match scope to what was requested."""


def make_actions_risk_section() -> PromptSection:
    """``# Executing actions with care`` — mirrors prompts.ts:257-269."""
    return PromptSection(
        name="actions_risk",
        compute=lambda _t, _c: _ACTIONS_RISK_BODY,
    )


# ────────────────────────  Tool availability (positive + negative)  ─────────


def make_using_your_tools_section() -> PromptSection:
    """``# Available Tools`` — delegates to ``render_tool_availability_section``.

    Defers to the existing renderer in ``builder.py`` so the
    positive-enumeration + negative-enumeration logic stays in one place.
    Returns the rendered string for the current ``ctx.enabled_tools``.
    """

    def _compute(tools: frozenset[str], ctx: PromptCtx) -> str | None:
        if not tools:
            return None
        return render_tool_availability_section(
            list(tools), lang=ctx.lang or "en"
        )

    # Mark cache_break=False — the tool list is per-session, not per-turn,
    # so within a single AgentRunner.run() the result is stable. But a
    # fresh runner gets a fresh cache, so per-session changes propagate.
    return PromptSection(name="using_your_tools", compute=_compute)


# ─────────────────────────  Supervisor delegation  ────────────────────────────


_SUPERVISOR_WORKFLOW_BODY = """\
# Workflow (how to approach any user request)

1. Classify the request: **read / understand**, **observe / summarize /
   write a note**, **execute a change**, **plan decision follow-up**, or
   **ambiguous**.
2. *read / understand* → answer directly using retrieval tools. Do not
   delegate for simple factual Q&A.
3. *observe / summarize / write a note* → spawn `sub_librarian`.
4. *execute a change* → spawn `sub_archivist`. The archivist will submit
   a plan when the change touches >3 documents or crosses KBs.
5. *plan decision follow-up* — current message starts with
   `[plan system]` — obey verbatim. Approval = spawn sub_archivist to
   execute via `get_pending_plan`. Rejection = acknowledge + stop.
   Request-changes = spawn archivist with the user's revision note so it
   re-submit_plans. These directives **override** any other
   classification.
6. *ambiguous* → follow the clarify-vs-act decision tree below."""


def make_supervisor_workflow_section() -> PromptSection:
    return PromptSection(
        name="supervisor_workflow",
        compute=lambda _t, _c: _SUPERVISOR_WORKFLOW_BODY,
    )


_SUPERVISOR_DELEGATION_BODY = """\
# Delegation rules (do NOT re-delegate)

- One user request → at most ONE subagent of each type. Spawning the
  same subagent twice for the same request wastes budget; avoid it.
- Do NOT call a write tool directly. You do not have `doc_tag`,
  `doc_archive`, etc. — reach them via
  `spawn_subagent(subagent_type=...)`.
- Pass complete briefs when spawning: the subagent cannot ask you
  for clarification mid-run.
- When a subagent returns, summarize its findings for the user in your
  own words. Do not verbatim-dump its output."""


def make_supervisor_delegation_section() -> PromptSection:
    """``# Delegation rules`` — only included when ``spawn_subagent`` enabled."""

    def _compute(tools: frozenset[str], _ctx: PromptCtx) -> str | None:
        if names.SPAWN_SUBAGENT not in tools:
            return None
        return _SUPERVISOR_DELEGATION_BODY

    return PromptSection(name="supervisor_delegation", compute=_compute)


_CLARIFY_VS_ACT_BODY = """\
# Clarify-vs-act decision tree

- Destructive or cross-KB change + ambiguous target → call
  `ask_user_question` BEFORE spawning `sub_archivist`.
- Batch of 5+ documents with clear intent → spawn `sub_archivist` and
  let it `submit_plan` for approval.
- Single-document change with unambiguous target → spawn
  `sub_archivist` directly.
- Pure read or audit → never clarify; answer directly."""


def make_clarify_vs_act_section() -> PromptSection:
    """``# Clarify-vs-act`` — supervisors with ask_user_question + spawn."""

    def _compute(tools: frozenset[str], _ctx: PromptCtx) -> str | None:
        if names.SPAWN_SUBAGENT not in tools:
            return None
        # ask_user_question optional — drop the bullet that mentions it if
        # absent. We grep the rendered text for the literal token name
        # since that's what's stamped into the body above.
        if names.ASK_USER_QUESTION not in tools:
            return "\n".join(
                line for line in _CLARIFY_VS_ACT_BODY.splitlines()
                if names.ASK_USER_QUESTION not in line
            )
        return _CLARIFY_VS_ACT_BODY

    return PromptSection(name="clarify_vs_act", compute=_compute)


# ──────────────────────────  Dynamic sections  ────────────────────────────────


def make_pending_plan_section() -> PromptSection:
    """``# Pending plan (current state)`` — cache-busts when status changes.

    Reads ``ctx.pending_plan_status``; ``None`` → no section emitted.
    Mirrors claude-code-ref's MCP_INSTRUCTIONS dynamic-attachment pattern
    where session-state-dependent content sits past the boundary.
    """

    def _compute(_tools: frozenset[str], ctx: PromptCtx) -> str | None:
        st = ctx.pending_plan_status
        if not st:
            return None
        return (
            f"# Pending plan (current state)\n\n"
            f"Session has a plan with status `{st}`. If status is "
            "`approved`, call `get_pending_plan` first to read back the "
            "plan body, then execute step-by-step. If `waiting`, do not "
            "execute writes — wait for `[plan approved]`. If `rejected` "
            "or `request_changes`, acknowledge and stop unless the user "
            "asks for revisions."
        )

    return PromptSection(
        name="pending_plan",
        compute=_compute,
        cache_break=True,
        reason=(
            "ctx.pending_plan_status changes per-turn after submit_plan; "
            "stale value would mis-direct the agent's next action"
        ),
    )


def make_kb_scope_section() -> PromptSection:
    """``# KB scope`` — current ``ctx.kb_ids``. Cache-busts on selection change."""

    def _compute(_tools: frozenset[str], ctx: PromptCtx) -> str | None:
        if not ctx.kb_ids:
            return None
        kb_list = ", ".join(f"`{k}`" for k in ctx.kb_ids)
        return (
            "# KB scope\n\n"
            f"You can retrieve / read from these knowledge bases this "
            f"turn: {kb_list}. Tool calls outside this scope will fail "
            "RBAC."
        )

    return PromptSection(
        name="kb_scope",
        compute=_compute,
        cache_break=True,
        reason=(
            "ctx.kb_ids may change between turns when user switches KB "
            "selection; outdated scope misleads tool calls"
        ),
    )


# ───────────────────────  Convenience preset assemblers  ──────────────────────


# Note on responsibilities:
#
# The presets below produce the **body** of an agent's system prompt — the
# stable "who you are / what you do / how to behave" content owned by the
# AgentDefinition. The runner (``AgentRunner._build_options``) is still
# responsible for appending two **per-session / per-turn** segments AFTER
# the boundary marker:
#
# 1. ``render_tool_availability_section(self.tool_names, lang=...)`` —
#    positive + negative tool enumeration. Per-session (changes only when
#    starting a new runner with a different tool set), so it doesn't need
#    to live in the cacheable body.
# 2. ``render_attachments_prompt_section(self.attachments, lang=...)`` —
#    per-turn (attachment list mutates each user turn).
#
# Keeping these in the runner means the AgentDefinition body stays a
# stable cacheable prefix; per-session and per-turn variation is appended
# below the boundary marker at runtime.


def supervisor_static_sections(
    *,
    extra_constraints: Iterable[str] | None = None,
) -> list[PromptSection]:
    """Standard static-section list for retrieval-flavored supervisors.

    Order: identity → domain → constraints → workflow → delegation →
    clarify → actions / tone / length → output.
    """
    return [
        make_identity_section(),
        make_domain_context_section(),
        make_strict_rag_constraints(extras=extra_constraints),
        make_supervisor_workflow_section(),
        make_supervisor_delegation_section(),
        make_clarify_vs_act_section(),
        make_actions_risk_section(),
        make_tone_and_style_section(),
        make_numeric_length_anchors(),
        make_retrieval_output_rules(),
    ]


def supervisor_dynamic_sections() -> list[PromptSection]:
    """Standard dynamic sections — currently empty (reserved for v0.15).

    Phase 2.8 v1.0 evaluates ``AgentDefinition.system_prompt`` ONCE at
    module-load time, so dynamic sections (``pending_plan_section``,
    ``kb_scope_section``) would never fire and are intentionally omitted
    here. v0.15 plans to defer system_prompt evaluation to per-turn so
    these can be wired in.

    The boundary marker (always emitted by ``assemble_prompt``) still
    serves a purpose: ``AgentRunner._build_options`` appends per-turn
    content (tool availability + attachments) AFTER the assembled body,
    so the boundary correctly separates the cacheable body from the
    runner-appended dynamic suffix.
    """
    return []


def operator_static_sections(
    *,
    mission: str,
) -> list[PromptSection]:
    """Standard static-section list for operator subagents (sub_archivist).

    Operators do not produce [N] citations; they get
    ``step_marker_rules`` + ``no_citation_for_operators`` in place of
    ``retrieval_output_rules``.
    """
    return [
        make_identity_section(),
        make_mission_section(mission),
        make_plan_gate_block(),
        make_actions_risk_section(),
        make_tone_and_style_section(),
        make_numeric_length_anchors(),
        make_step_marker_rules(),
        make_no_citation_for_operators(),
    ]


def reflective_static_sections(
    *,
    mission: str,
    extra_constraints: Iterable[str] | None = None,
) -> list[PromptSection]:
    """Standard static-section list for read-only reflective subagents
    (sub_librarian / sub_evidence_checker / sub_policy_researcher).

    Includes the READ-ONLY block; allows [N] citations by default.
    """
    return [
        make_identity_section(),
        make_mission_section(mission),
        make_read_only_block(),
        make_strict_rag_constraints(extras=extra_constraints),
        make_tone_and_style_section(),
        make_numeric_length_anchors(),
        make_retrieval_output_rules(),
    ]
