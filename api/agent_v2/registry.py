"""工具聚合 — 构造一个 MCP In-Process Server 供 SDK 使用。

Phase 2.6 v0.5 在这里统一给每个工具注入两件东西：

- **searchHint 前缀**（来自 ``prompting.SEARCH_HINT_BY_TOOL``）：一句 3-10 词
  的祈使句贴到 description 最前，等价于 Claude Code 的 `searchHint`。LLM 在
  第一眼就能看到工具意图，不必解析大段 Usage notes 才能辨识。
- **MCP annotations**（来自 ``annotations.ANNOTATIONS``）：MCP 协议的
  ``readOnly`` / ``destructive`` / ``openWorld`` 布尔位，传给模型供其调度。

做在 registry 层而不是 tool 源文件里，是为了让 17 个工具的 description 保持
简洁，而且 searchHint / annotation 调整只改一处。
"""

from __future__ import annotations

import copy

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server
from claude_agent_sdk.types import McpToolAnnotations

from .tools import _names as names
from .tools.ask_user_question import ask_user_question
from .tools.doc_ops import (
    doc_archive,
    doc_ingest_attachment,
    doc_create_note,
    doc_list_recent_changes,
    doc_rename,
    doc_reparse,
    doc_tag,
    doc_upload_from_url,
    kb_audit,
    kb_create,
    kb_stats,
)
from .tools.get_pending_plan import get_pending_plan
from .tools.rag_graph_query import rag_graph_query
from .tools.rag_list_docs import rag_list_docs
from .tools.rag_read_doc import rag_read_doc
from .tools.rag_retrieve import rag_retrieve
from .tools.spawn_subagent import spawn_subagent
from .tools.submit_plan import submit_plan
from .tools.web_fetch import web_fetch
from .tools.web_fetch_to_attachment import web_fetch_to_attachment
from .tools.web_search import web_search

# 所有已实现工具的注册表 — 工具名一律走 ``_names`` 常量，禁用裸字面量。
ALL_TOOLS = {
    # 读：RAG 检索
    names.RAG_RETRIEVE: rag_retrieve,
    names.RAG_LIST_DOCS: rag_list_docs,
    names.RAG_READ_DOC: rag_read_doc,
    names.RAG_GRAPH_QUERY: rag_graph_query,
    # 委派
    names.SPAWN_SUBAGENT: spawn_subagent,
    # Phase 2.6 — 文档写操作（sub_archivist 专用）
    names.DOC_TAG: doc_tag,
    names.DOC_RENAME: doc_rename,
    names.DOC_ARCHIVE: doc_archive,
    names.DOC_REPARSE: doc_reparse,
    names.DOC_UPLOAD_FROM_URL: doc_upload_from_url,
    names.KB_CREATE: kb_create,
    # Phase 2.6 v0.2 — 自我维护 / 总结笔记（sub_librarian 专用)
    names.DOC_CREATE_NOTE: doc_create_note,
    names.KB_AUDIT: kb_audit,
    names.KB_STATS: kb_stats,
    names.DOC_LIST_RECENT_CHANGES: doc_list_recent_changes,
    # Phase 2.6 — 交互式工具（两个 subagent 共用）
    names.ASK_USER_QUESTION: ask_user_question,
    names.SUBMIT_PLAN: submit_plan,
    # Phase 2.6 v0.6 — plan 执行闭环（sub_archivist 在批准后调用）
    names.GET_PENDING_PLAN: get_pending_plan,
    # Phase 2.6 v0.7 — 公网检索 / 抓取（研究类 supervisor / subagent 可选）
    names.WEB_SEARCH: web_search,
    names.WEB_FETCH: web_fetch,
    # Phase 2.7 Stage 2 — attachment materialize + archive
    names.WEB_FETCH_TO_ATTACHMENT: web_fetch_to_attachment,
    names.DOC_INGEST_ATTACHMENT: doc_ingest_attachment,
}

# MCP server 名（给 SDK 用）；SDK 生成的工具名是 ``mcp__<server>__<tool>``
MCP_SERVER_NAME = "ragflow"


def list_tool_names() -> list[str]:
    """返回所有 Agent 可见的 MCP 工具名（带 SDK 前缀）。"""
    return [f"mcp__{MCP_SERVER_NAME}__{n}" for n in ALL_TOOLS]


_HINT_MARKER = "[intent]"
_AGENT_LISTING_MARKER = "Available subagent types and the tools they have access to:"


def _format_subagent_line(defn) -> str:
    """Mirror claude-code-ref's ``formatAgentLine`` (packages/builtin-tools/
    src/tools/AgentTool/prompt.ts:43): ``- name: when_to_use (Tools: ...)``.

    Translates ``AgentDefinition.tools`` ("*" / list / []) +
    ``disallowed_tools`` into a human-readable tool description so the
    spawning agent can see — at the spawn_subagent description level —
    exactly which tools each subagent has, instead of hallucinating that
    "subagents share my toolset".
    """
    tools = defn.tools
    deny = tuple(defn.disallowed_tools or ())

    if tools == "*":
        if deny:
            tools_str = "All tools except " + ", ".join(deny)
        else:
            tools_str = "All tools"
    elif isinstance(tools, list):
        if deny:
            effective = [t for t in tools if t not in set(deny)]
        else:
            effective = list(tools)
        tools_str = ", ".join(effective) if effective else "None"
    else:
        tools_str = "Unknown"

    when = (defn.when_to_use or defn.description or "").strip().replace("\n", " ")
    return f"- {defn.name}: {when} (Tools: {tools_str})"


def _build_subagent_listing() -> str | None:
    """Render registered subagent definitions as a Claude Code-style listing.

    Returns ``None`` when no subagents are registered (testing / minimal
    deploys), so callers can skip injecting the section.
    """
    from .definitions.registry import list_definitions

    subs = [d for d in list_definitions(kind="subagent")]
    if not subs:
        return None
    lines = "\n".join(_format_subagent_line(d) for d in subs)
    return f"{_AGENT_LISTING_MARKER}\n{lines}"


def _decorate_for_mcp(tool: SdkMcpTool) -> SdkMcpTool:
    """Return a copy of ``tool`` with searchHint prefix + MCP annotations set.

    Idempotent: if description already starts with ``_HINT_MARKER`` or
    ``annotations`` is already populated, those channels are left alone. We
    copy first so the underlying module-level ``@tool`` objects stay clean
    — tests introspect the original descriptions.

    For ``spawn_subagent`` specifically we also append a Claude Code-style
    "Available subagent types ..." block listing each registered subagent's
    tool whitelist, so the parent agent does not have to guess what each
    subagent can or cannot do (mirrors
    claude-code-ref/packages/builtin-tools/src/tools/AgentTool/prompt.ts).
    """
    from .annotations import ANNOTATIONS
    from .prompting import SEARCH_HINT_BY_TOOL

    decorated = copy.copy(tool)
    description = tool.description

    # (1) Subagent listing — only for spawn_subagent
    if tool.name == names.SPAWN_SUBAGENT and _AGENT_LISTING_MARKER not in description:
        listing = _build_subagent_listing()
        if listing:
            description = f"{description}\n\n{listing}"

    # (2) searchHint prefix
    hint = SEARCH_HINT_BY_TOOL.get(tool.name)
    if hint and not description.lstrip().startswith(_HINT_MARKER):
        description = f"{_HINT_MARKER} {hint}\n\n{description}"

    decorated.description = description

    # (3) MCP protocol annotations (readOnly / destructive / openWorld)
    ann = ANNOTATIONS.get(tool.name)
    if ann is not None and decorated.annotations is None:
        mcp_ann: McpToolAnnotations = {
            "readOnly": ann.is_read_only,
            "destructive": not ann.is_read_only and not ann.is_idempotent,
            # openWorld = reaches beyond the KB (network fetch, user IO).
            # True for URL ingest / interactive / public web tools.
            "openWorld": tool.name
            in (
                names.DOC_UPLOAD_FROM_URL,
                names.ASK_USER_QUESTION,
                names.SUBMIT_PLAN,
                names.WEB_SEARCH,
                names.WEB_FETCH,
                names.WEB_FETCH_TO_ATTACHMENT,  # network download
                # doc_ingest_attachment does NOT hit the internet — it
                # reads from MinIO + writes to the KB + DB, which is all
                # in-cluster. Not openWorld.
            ),
        }
        decorated.annotations = mcp_ann

    return decorated


def build_mcp_server(enabled: list[str] | None = None):
    """构造 MCP In-Process Server。

    Args:
        enabled: 仅启用这些工具名（短名，如 ``["rag_retrieve"]``）。
                 ``None`` 表示启用全部已实现工具。

    Returns:
        ``McpSdkServerConfig`` 可直接传给 ``ClaudeAgentOptions.mcp_servers``。
    """
    if enabled is None:
        raw_tools = list(ALL_TOOLS.values())
    else:
        raw_tools = [ALL_TOOLS[n] for n in enabled if n in ALL_TOOLS]
    tools = [_decorate_for_mcp(t) for t in raw_tools]
    return create_sdk_mcp_server(MCP_SERVER_NAME, "0.1.0", tools=tools)
