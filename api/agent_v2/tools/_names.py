"""Single source of truth for tool name strings.

All prompt-rendering code, registry, annotations, and tests must import
constants from here rather than using bare string literals. Renaming a
tool then flows through one file.

Mirrors claude-code-ref's per-tool ``toolName.ts`` / ``constants.ts``
pattern (e.g. ``BASH_TOOL_NAME``, ``FILE_READ_TOOL_NAME``,
``ASK_USER_QUESTION_TOOL_NAME``) but consolidated into one module —
RAGFlow has 22 tools, scattering 22 files for one constant each is
overkill.

Phase 2.8 / D3 — see ``AUDIT-claude-code-alignment.md`` §五-e and
``PLAN-prompt-architecture.md`` §5.1.
"""

from __future__ import annotations


# ──────────────────────────────  Read (KB)  ──────────────────────────────

RAG_RETRIEVE = "rag_retrieve"
RAG_LIST_DOCS = "rag_list_docs"
RAG_READ_DOC = "rag_read_doc"
RAG_GRAPH_QUERY = "rag_graph_query"


# ─────────────────────  Delegation / interaction  ──────────────────────

SPAWN_SUBAGENT = "spawn_subagent"
ASK_USER_QUESTION = "ask_user_question"
SUBMIT_PLAN = "submit_plan"
GET_PENDING_PLAN = "get_pending_plan"


# ────────────────────────────  Reflect / observe  ──────────────────────

KB_STATS = "kb_stats"
KB_AUDIT = "kb_audit"
DOC_LIST_RECENT_CHANGES = "doc_list_recent_changes"


# ────────────────────────  Write — destructive ops  ────────────────────

DOC_TAG = "doc_tag"
DOC_RENAME = "doc_rename"
DOC_ARCHIVE = "doc_archive"
DOC_REPARSE = "doc_reparse"
DOC_UPLOAD_FROM_URL = "doc_upload_from_url"
KB_CREATE = "kb_create"
DOC_CREATE_NOTE = "doc_create_note"


# ──────────────────────────  Web (open-world)  ─────────────────────────

WEB_SEARCH = "web_search"
WEB_FETCH = "web_fetch"


# ──────────────────────────  Attachment flow  ──────────────────────────

WEB_FETCH_TO_ATTACHMENT = "web_fetch_to_attachment"
DOC_INGEST_ATTACHMENT = "doc_ingest_attachment"


# ────────────────────  Convenience tuples (immutable)  ─────────────────

ALL_READ_TOOLS: tuple[str, ...] = (
    RAG_RETRIEVE,
    RAG_LIST_DOCS,
    RAG_READ_DOC,
    RAG_GRAPH_QUERY,
)

ALL_REFLECT_TOOLS: tuple[str, ...] = (
    KB_STATS,
    KB_AUDIT,
    DOC_LIST_RECENT_CHANGES,
)

ALL_WRITE_TOOLS: tuple[str, ...] = (
    DOC_TAG,
    DOC_RENAME,
    DOC_ARCHIVE,
    DOC_REPARSE,
    DOC_UPLOAD_FROM_URL,
    KB_CREATE,
    DOC_CREATE_NOTE,
    DOC_INGEST_ATTACHMENT,
)

ALL_INTERACTIVE_TOOLS: tuple[str, ...] = (
    ASK_USER_QUESTION,
    SUBMIT_PLAN,
    GET_PENDING_PLAN,
)

ALL_WEB_TOOLS: tuple[str, ...] = (
    WEB_SEARCH,
    WEB_FETCH,
    WEB_FETCH_TO_ATTACHMENT,
)


# Full registry — keep ordered for deterministic prompt rendering.
ALL_TOOL_NAMES: tuple[str, ...] = (
    # Read
    RAG_RETRIEVE,
    RAG_LIST_DOCS,
    RAG_READ_DOC,
    RAG_GRAPH_QUERY,
    # Delegation / interaction
    SPAWN_SUBAGENT,
    ASK_USER_QUESTION,
    SUBMIT_PLAN,
    GET_PENDING_PLAN,
    # Reflect
    KB_STATS,
    KB_AUDIT,
    DOC_LIST_RECENT_CHANGES,
    # Write
    DOC_TAG,
    DOC_RENAME,
    DOC_ARCHIVE,
    DOC_REPARSE,
    DOC_UPLOAD_FROM_URL,
    KB_CREATE,
    DOC_CREATE_NOTE,
    # Web
    WEB_SEARCH,
    WEB_FETCH,
    # Attachments
    WEB_FETCH_TO_ATTACHMENT,
    DOC_INGEST_ATTACHMENT,
)
