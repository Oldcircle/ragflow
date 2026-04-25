"""Phase 2.6 v0.4 — tool annotations (U8 from AUDIT-claude-code-alignment.md).

The Claude Agent SDK ``@tool`` decorator only carries ``name / description /
input_schema``. Claude Code ships extra metadata (``is_read_only``,
``is_idempotent``, cost bucket, latency hint, side-effects) alongside each
tool so the model can weigh them when planning. We can't extend the SDK, so
we mirror that metadata in an out-of-band registry and splice a summary into
the supervisor system prompt.

Usage:

    from .annotations import ANNOTATIONS, annotations_summary_for_prompt

    text = annotations_summary_for_prompt(["rag_retrieve", "doc_tag", ...])
    # → renders a compact table the LLM can read.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tools import _names as names


@dataclass(frozen=True)
class ToolAnnotation:
    """Out-of-band metadata for a tool.

    Fields mirror the Claude Code conventions where they apply to our domain:

    - ``is_read_only``: the tool does not mutate tenant / KB / doc state. Read
      tools can be retried freely; writes cannot.
    - ``is_idempotent``: replaying the exact same args within a short window
      yields the same effect. Writes with an ``idempotency_key`` param are
      idempotent; ones that queue async work (reparse) are not.
    - ``cost_class``: rough LLM-facing cost bucket.
        - ``cheap``     — <100ms p50, minimal DB load; prefer these for probing
        - ``normal``    — 100ms–1s p50, few DB reads + 1 LLM call possible
        - ``expensive`` — >1s p50, background job, embedding, network I/O
    - ``avg_latency_ms``: rough p50 in the best path (no cache miss, no
      retries). Used only as a hint; not enforced.
    - ``side_effects``: human-readable list describing what the tool changes.
      Empty for read-only tools.
    - ``supports_next_steps``: ``True`` when the tool's ``ok()`` envelope
      routinely includes a ``next_steps`` list. Lets the prompt builder tell
      the LLM which outputs to look for.
    """

    name: str
    is_read_only: bool
    is_idempotent: bool
    cost_class: str
    avg_latency_ms: int
    side_effects: tuple[str, ...] = ()
    supports_next_steps: bool = False

    def one_liner(self) -> str:
        """Compact row for the supervisor system-prompt table."""
        rw = "R" if self.is_read_only else "W"
        idem = "idem" if self.is_idempotent else "NOT-idem"
        return (
            f"- {self.name}: {rw}/{idem}/{self.cost_class}"
            f" (~{self.avg_latency_ms}ms)"
        )


# ────────────────────────────── registry ──────────────────────────────
#
# When you add a new tool to ``api/agent_v2/registry.ALL_TOOLS`` you MUST add
# its annotation below as well. ``test_annotations.py`` asserts parity.

ANNOTATIONS: dict[str, ToolAnnotation] = {
    # ── Read / RAG ──────────────────────────────────────────────────────
    names.RAG_RETRIEVE: ToolAnnotation(
        name=names.RAG_RETRIEVE,
        is_read_only=True,
        is_idempotent=True,
        cost_class="normal",
        avg_latency_ms=400,
        # chunks carry doc_id → natural next_step is rag_read_doc or doc_tag
        supports_next_steps=True,
    ),
    names.RAG_LIST_DOCS: ToolAnnotation(
        name=names.RAG_LIST_DOCS,
        is_read_only=True,
        is_idempotent=True,
        cost_class="cheap",
        avg_latency_ms=80,
        supports_next_steps=True,
    ),
    names.RAG_READ_DOC: ToolAnnotation(
        name=names.RAG_READ_DOC,
        is_read_only=True,
        is_idempotent=True,
        cost_class="cheap",
        avg_latency_ms=120,
        supports_next_steps=True,
    ),
    names.RAG_GRAPH_QUERY: ToolAnnotation(
        name=names.RAG_GRAPH_QUERY,
        is_read_only=True,
        is_idempotent=True,
        cost_class="normal",
        avg_latency_ms=350,
        supports_next_steps=True,
    ),
    # ── Delegation ──────────────────────────────────────────────────────
    names.SPAWN_SUBAGENT: ToolAnnotation(
        name=names.SPAWN_SUBAGENT,
        is_read_only=False,  # the child may write
        is_idempotent=False,
        cost_class="expensive",
        avg_latency_ms=6000,
        side_effects=("runs a child agent with its own LLM budget",),
    ),
    # ── Write / doc ops ─────────────────────────────────────────────────
    names.DOC_TAG: ToolAnnotation(
        name=names.DOC_TAG,
        is_read_only=False,
        is_idempotent=True,  # add/remove/set are replay-safe
        cost_class="normal",
        avg_latency_ms=200,
        side_effects=("mutates doc.meta_fields.tags",),
        supports_next_steps=True,
    ),
    names.DOC_RENAME: ToolAnnotation(
        name=names.DOC_RENAME,
        is_read_only=False,
        is_idempotent=True,
        cost_class="cheap",
        avg_latency_ms=120,
        side_effects=("mutates document.name",),
        supports_next_steps=True,
    ),
    names.DOC_ARCHIVE: ToolAnnotation(
        name=names.DOC_ARCHIVE,
        is_read_only=False,
        is_idempotent=False,  # a second call moves the re-created doc again
        cost_class="expensive",
        avg_latency_ms=1500,
        side_effects=(
            "creates a new Document row in target KB",
            "deletes the original Document row from source KB",
            "moves chunks between indexes",
        ),
        supports_next_steps=True,
    ),
    names.DOC_REPARSE: ToolAnnotation(
        name=names.DOC_REPARSE,
        is_read_only=False,
        is_idempotent=False,  # queues a fresh task even on replay
        cost_class="expensive",
        avg_latency_ms=2000,
        side_effects=(
            "clears existing chunks",
            "enqueues background parsing + embedding",
        ),
        supports_next_steps=True,
    ),
    names.DOC_UPLOAD_FROM_URL: ToolAnnotation(
        name=names.DOC_UPLOAD_FROM_URL,
        is_read_only=False,
        is_idempotent=True,  # URL+kb dedup guards replays
        cost_class="expensive",
        avg_latency_ms=4000,
        side_effects=(
            "downloads remote content",
            "creates Document + triggers parsing",
        ),
        supports_next_steps=True,
    ),
    names.KB_CREATE: ToolAnnotation(
        name=names.KB_CREATE,
        is_read_only=False,
        is_idempotent=False,
        cost_class="normal",
        avg_latency_ms=300,
        side_effects=(
            "creates Knowledgebase row",
            "grants caller OWNER access",
        ),
        supports_next_steps=True,
    ),
    # ── Reflect / summarize (librarian) ─────────────────────────────────
    names.DOC_CREATE_NOTE: ToolAnnotation(
        name=names.DOC_CREATE_NOTE,
        is_read_only=False,
        is_idempotent=True,  # dedup by (kb_id, title)
        cost_class="normal",
        avg_latency_ms=400,
        side_effects=(
            "creates Document with a synthetic markdown note",
            "triggers parsing",
        ),
        supports_next_steps=True,
    ),
    names.KB_AUDIT: ToolAnnotation(
        name=names.KB_AUDIT,
        is_read_only=True,
        is_idempotent=True,
        cost_class="normal",
        avg_latency_ms=600,
    ),
    names.KB_STATS: ToolAnnotation(
        name=names.KB_STATS,
        is_read_only=True,
        is_idempotent=True,
        cost_class="cheap",
        avg_latency_ms=90,
    ),
    names.DOC_LIST_RECENT_CHANGES: ToolAnnotation(
        name=names.DOC_LIST_RECENT_CHANGES,
        is_read_only=True,
        is_idempotent=True,
        cost_class="cheap",
        avg_latency_ms=80,
    ),
    # ── Interactive ─────────────────────────────────────────────────────
    names.ASK_USER_QUESTION: ToolAnnotation(
        name=names.ASK_USER_QUESTION,
        is_read_only=True,  # doesn't touch KB state; SSE-only
        is_idempotent=False,  # each call opens a distinct prompt
        cost_class="cheap",
        avg_latency_ms=20,
        side_effects=(
            "emits SSE event; halts the turn to wait for user reply",
        ),
    ),
    names.SUBMIT_PLAN: ToolAnnotation(
        name=names.SUBMIT_PLAN,
        is_read_only=False,  # flips session.pending_plan_status
        is_idempotent=False,
        cost_class="cheap",
        avg_latency_ms=40,
        side_effects=(
            "persists pending_plan on AgentV2Session",
            "emits SSE plan_submitted event",
            "locks destructive writes until the user decides",
        ),
    ),
    names.GET_PENDING_PLAN: ToolAnnotation(
        name=names.GET_PENDING_PLAN,
        is_read_only=True,
        is_idempotent=True,
        cost_class="cheap",
        avg_latency_ms=40,
    ),
    # ── Web（Phase 2.6 v0.7）──────────────────────────────────────────────
    names.WEB_SEARCH: ToolAnnotation(
        name=names.WEB_SEARCH,
        is_read_only=True,
        # Not truly idempotent — web index changes — but retrying the same
        # query is replay-safe within a short window, so treat as idempotent
        # for LLM planning purposes.
        is_idempotent=True,
        cost_class="normal",
        avg_latency_ms=1500,
        side_effects=("calls external Tavily API; counts against quota",),
    ),
    names.WEB_FETCH: ToolAnnotation(
        name=names.WEB_FETCH,
        is_read_only=True,
        is_idempotent=True,  # same URL → (usually) same content
        cost_class="normal",
        avg_latency_ms=2500,
        side_effects=("HTTP GET to the public internet",),
    ),
    # ── Attachments (Phase 2.7 Stage 2) ────────────────────────────────
    names.WEB_FETCH_TO_ATTACHMENT: ToolAnnotation(
        name=names.WEB_FETCH_TO_ATTACHMENT,
        # Technically writes a DB row + MinIO blob, but no KB change yet —
        # the materialization is a **staging** act, reversible via reject.
        # Treat as write for openWorld / destructive signals, but keep
        # idempotent=True because 24h URL dedupe makes replays safe.
        is_read_only=False,
        is_idempotent=True,
        cost_class="normal",
        avg_latency_ms=3500,
        side_effects=(
            "HTTP GET to the public internet",
            "creates a staged AgentV2Attachment row + MinIO blob",
        ),
        supports_next_steps=True,
    ),
    names.DOC_INGEST_ATTACHMENT: ToolAnnotation(
        name=names.DOC_INGEST_ATTACHMENT,
        is_read_only=False,
        # Idempotent: same attachment_id archives to same KB → status=
        # already_archived with the existing doc_id, no duplicate write.
        is_idempotent=True,
        cost_class="expensive",
        avg_latency_ms=1500,
        side_effects=(
            "creates Document + chunks + triggers parse queue",
            "flips AgentV2Attachment.status to archived",
        ),
        supports_next_steps=True,
    ),
}


def annotations_summary_for_prompt(tool_names: list[str] | tuple[str, ...] | None) -> str:
    """Render a compact table the supervisor system prompt can paste in.

    ``None`` means "all registered tools". Unknown names are skipped silently
    — the caller shouldn't crash a prompt build over a typo.
    """
    if tool_names is None:
        names: list[str] = list(ANNOTATIONS.keys())
    else:
        names = [n for n in tool_names if n in ANNOTATIONS]
    if not names:
        return ""
    lines = [
        "Tool budget hints (R=read / W=write, idem=safe to replay, latency is p50):",
    ]
    for name in names:
        lines.append(ANNOTATIONS[name].one_liner())
    lines.append(
        "Prefer cheap + read-only tools for exploration. Use expensive "
        "tools (spawn_subagent, doc_upload_from_url, doc_reparse) only "
        "when the cheaper alternative cannot answer the question."
    )
    return "\n".join(lines)


__all__ = [
    "ToolAnnotation",
    "ANNOTATIONS",
    "annotations_summary_for_prompt",
]
