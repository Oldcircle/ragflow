"""Prompt builders — Phase 2.8 PromptSection architecture.

## Design overview

Phase 2.8 (see ``PLAN-prompt-architecture.md``) replaced the v0.3 monolithic
string-template approach with a section-list model that mirrors
``vendor/claude-code-ref/src/constants/systemPromptSections.ts``:

- **``PromptSection``** is a named, optionally-cacheable unit. Each compute
  callback receives ``(enabled_tools, ctx)`` and returns a string or ``None``
  (``None`` segments drop out of the final prompt).
- **``PromptCtx``** carries per-render variables (``role_line`` /
  ``enabled_tools`` / ``pending_plan_status`` etc.).
- **``PromptCache``** memoizes ``PromptSection`` outputs that are not
  ``cache_break``. Mirrors ref's ``getSystemPromptSectionCache`` flow.
- **``SYSTEM_PROMPT_DYNAMIC_BOUNDARY``** splits the assembled prompt into a
  static prefix (memoizable) and a dynamic suffix (recomputed per turn).
  Prefix stability enables prompt-cache hits on DeepSeek and Anthropic
  providers.

The legacy ``build_supervisor_prompt`` / ``build_subagent_prompt`` helpers are
preserved as backward-compat shims so existing AgentDefinition files keep
working while we migrate them stage-by-stage.

References:
- ``vendor/claude-code-ref/src/constants/systemPromptSections.ts:8-58``
- ``vendor/claude-code-ref/src/constants/prompts.ts:116-117`` + ``:564-580``
- ``AUDIT-claude-code-alignment.md`` §五-e
- ``PLAN-prompt-architecture.md``
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Iterable

from ..tools import _names as names


# ──────────────────────────────  Phase 2.8 core  ──────────────────────────────


SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"
"""Literal marker delimiting cacheable prefix from per-turn suffix.

Mirrors ``vendor/claude-code-ref/src/constants/prompts.ts:116-117``. Provider
adapters can split on this to apply ``cache_control``; consumers that don't
care just get one string.
"""


@dataclass
class PromptCtx:
    """Render context passed to every section's ``compute`` callback."""

    role_line: str = ""
    """Single sentence identity, e.g. 'You are sub_archivist...'."""

    enabled_tools: frozenset[str] = field(default_factory=frozenset)
    """Tool short-names this session can reach (D2 fix)."""

    domain_context: str = ""
    """Optional paragraph describing the agent's KB / domain."""

    domain_extras: str = ""
    """Raw markdown appended after standard sections."""

    pending_plan_status: str | None = None
    """Current ``AgentV2Session.pending_plan_status`` if any. Cache-busts
    pending_plan_section across turns."""

    kb_ids: tuple[str, ...] = ()
    """Active KB ids — cache-busts kb-aware sections on selection change."""

    lang: str = "en"
    """``en`` (default) or ``zh`` — drives tool_availability text."""


@dataclass
class PromptSection:
    """A named, optionally-cacheable prompt section.

    Mirrors claude-code-ref's ``systemPromptSection`` factory
    (``src/constants/systemPromptSections.ts:20``).

    Differences:
    - ``compute`` takes ``(enabled_tools, ctx)`` rather than zero args.
    - ``cache_break`` requires a non-empty ``reason`` to force authors to
      justify cache invalidation (matches the ``DANGEROUS_`` prefix intent
      from ``systemPromptSections.ts:32``).
    """

    name: str
    compute: Callable[[frozenset[str], PromptCtx], str | None]
    cache_break: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if self.cache_break and not self.reason:
            raise ValueError(
                f"PromptSection({self.name!r}, cache_break=True) requires "
                "a non-empty `reason` explaining why cache-busting is "
                "necessary."
            )


class PromptCache:
    """Per-runner memoization for ``PromptSection`` outputs.

    Mirrors claude-code-ref's cache flow
    (``systemPromptSections.ts:46-58``). Fresh cache per
    ``AgentRunner.run()``; ``cache_break=True`` sections always recompute.
    """

    def __init__(self) -> None:
        self._store: dict[str, str | None] = {}

    def resolve(
        self,
        sections: Sequence[PromptSection],
        ctx: PromptCtx,
    ) -> list[str]:
        """Return rendered strings for ``sections`` (Nones filtered)."""
        out: list[str] = []
        for s in sections:
            if s.cache_break or s.name not in self._store:
                self._store[s.name] = s.compute(ctx.enabled_tools, ctx)
            v = self._store[s.name]
            if v is not None:
                out.append(v)
        return out

    def clear(self) -> None:
        self._store.clear()

    def has(self, name: str) -> bool:
        return name in self._store


def assemble_prompt(
    static_sections: Sequence[PromptSection],
    dynamic_sections: Sequence[PromptSection],
    ctx: PromptCtx,
    cache: PromptCache | None = None,
) -> str:
    """Assemble a full system prompt using the Phase 2.8 layout.

    Layout::

        [static_sections joined with \\n\\n]
        __SYSTEM_PROMPT_DYNAMIC_BOUNDARY__
        [dynamic_sections joined with \\n\\n]

    The boundary marker stays in the output even when ``dynamic_sections`` is
    empty — provider adapters use it to split for ``cache_control`` insertion.

    Mirrors ``vendor/claude-code-ref/src/constants/prompts.ts:564-580``.
    """
    cache = cache if cache is not None else PromptCache()
    parts: list[str] = []
    parts.extend(cache.resolve(static_sections, ctx))
    parts.append(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    parts.extend(cache.resolve(dynamic_sections, ctx))
    return "\n\n".join(parts)


def prepend_bullets(items: Iterable[str | Iterable[str]]) -> list[str]:
    """Render a mixed flat / nested list as Markdown bullets.

    Mirrors ``vendor/claude-code-ref/src/constants/prompts.ts:169-175``.
    Nested items get 2-space indent.
    """
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(f"- {item}")
        else:
            for sub in item:
                out.append(f"  - {sub}")
    return out


# ──────────────────────────────  Shared constants  ──────────────────────────────


STRICT_RAG_CONSTRAINTS: list[str] = [
    "All factual claims (numbers, dates, durations, percentages, monetary amounts) "
    "MUST be supported by retrieved source chunks. Never supplement from training knowledge.",
    "If a required fact is not in the retrieved material, say so explicitly using the "
    "phrase 'no direct basis in the knowledge base' — do not guess, round, or infer.",
    "Never fabricate document names, authority names, section numbers, or dates.",
    "Never generalize a city's policy to a different city, or a version's rule to a "
    "different version.",
    # Phase 2.6 v0.8.3 — stop-on-empty guard (参照 Claude Code `prompts.ts:235`)
    # 问题根因：Q13 Agent 被诱导去"解读虚构文件号"，11 次 rag_retrieve / list_docs
    # / read_doc 循环切关键词无果后 subprocess crash。加一条硬约束让 Agent 主动
    # 收手，而不是像 general-purpose agent 只说 "multiple search strategies"。
    "If `rag_retrieve` returns 0 chunks (or all chunks have similarity < 0.2) "
    "for the same concept on TWO consecutive attempts with different keywords, "
    "STOP searching and answer 'no direct basis in the knowledge base'. Do not "
    "keep varying keywords indefinitely — the information is not in the KB. "
    "Three attempts is the hard maximum; beyond that you waste budget and risk "
    "subprocess timeout.",
    "Do not call `rag_list_docs` followed by `rag_read_doc` in a last-ditch "
    "attempt to find a document the user named by a specific 令号 / 文号 / "
    "条款号 that retrieval already failed to locate. If retrieval missed it, "
    "reading unrelated docs will not help — concede the miss clearly.",
]


RETRIEVAL_OUTPUT_RULES: list[str] = [
    "Annotate every factual sentence with a footnote marker like [1], [2], [3] "
    "corresponding to the order chunks appeared in rag_retrieve / rag_read_doc "
    "results this turn. Multiple markers like [1][2] are allowed when a sentence "
    "draws on multiple chunks.",
    "Do NOT inline file names in the answer body. The frontend renders a "
    '"Sources" panel automatically from the cited chunk ids.',
    "Do NOT annotate sentences that are commentary, opinion, hedging, or a "
    "summary — only sentences that assert a verifiable fact get a [N] marker.",
    "Do NOT repeat a list of source files at the end of the answer; the [N] "
    "markers already establish attribution.",
]


# searchHint-style short imperatives. Third-person intent phrases, 3-10 words,
# lowercase, no trailing punctuation. Used when we eventually adopt `shouldDefer`
# or need to help a classifier pick the right tool.
SEARCH_HINT_BY_TOOL: dict[str, str] = {
    # Retrieval
    names.RAG_RETRIEVE: "search knowledge base semantically for relevant chunks",
    names.RAG_LIST_DOCS: "list documents in the current knowledge base",
    names.RAG_READ_DOC: "read the full text of a single document",
    names.RAG_GRAPH_QUERY: "query the knowledge graph for an entity",
    # Delegation / interaction
    names.SPAWN_SUBAGENT: "delegate a focused task to a subagent",
    names.ASK_USER_QUESTION: "ask the user a multiple-choice clarifying question",
    names.SUBMIT_PLAN: "submit a plan for user approval before executing a batch",
    # Reflect / observe
    names.KB_STATS: "get quick KB metrics (doc count, chunks, embed coverage, <1KB)",
    names.KB_AUDIT: "deep KB audit with stale / duplicate / unparsed samples",
    names.DOC_LIST_RECENT_CHANGES: "list recent audit-log entries for this tenant",
    names.GET_PENDING_PLAN: "read back the approved plan to execute step by step",
    # Write
    names.DOC_CREATE_NOTE: "save agent-authored markdown as a new document",
    names.DOC_TAG: "add, remove, or replace document tags",
    names.DOC_RENAME: "rename a document",
    names.DOC_ARCHIVE: "move a document to another knowledge base",
    names.DOC_REPARSE: "clear chunks and re-run the parser on a document",
    names.DOC_UPLOAD_FROM_URL: "download a url into the knowledge base",
    names.KB_CREATE: "create a new empty knowledge base",
    # Web (Phase 2.6 v0.7)
    names.WEB_SEARCH: "search the public web for recent or external info",
    names.WEB_FETCH: "fetch the full text of a specific web page",
    # Attachments (Phase 2.7)
    names.WEB_FETCH_TO_ATTACHMENT: "download a url into a staged session attachment",
    names.DOC_INGEST_ATTACHMENT: "commit a staged session attachment into a knowledge base",
}


# ─────────────── Tool availability section (Phase 2.6 v0.8) ───────────────
#
# 抄 Claude Code 的 `getUsingYourToolsSection` 做法（src/constants/prompts.ts:
# 271-316）—— 在 system prompt 里显式枚举"这个会话你可以用哪些工具"。
# 和 Claude Code 不同之处：我们加**负面枚举**（"你没有 Gmail / Drive / LSP /
# Skill / Bash 等"），因为 DeepSeek 训练数据里大量提及 Claude 产品线工具，被问
# "你有什么工具"时会从训练记忆捞工具清单幻觉出来——Claude 官方模型不会有这个
# 问题，但我们跑的是 DeepSeek，需要更硬的防御。

# 常见被 DeepSeek 幻觉为"自己有"的工具名。这些在 Claude Code / Anthropic 产品
# 线文档里是存在的，但在我们这个 Python MCP server 里**不存在**。
_CONFABULATED_TOOLS: list[str] = [
    # Google workspace（Claude Code 有 MCP adapter，我们没注册）
    "Gmail",
    "Google Drive",
    "Google Calendar",
    # Claude Code 原生 IDE / dev 工具
    "Bash", "Shell", "Terminal",
    "Read", "Write", "Edit", "NotebookEdit",
    "LS", "Glob", "Grep",
    "LSP",
    "Skill",
    "SlashCommand",
    "TodoWrite", "Task", "Agent",
    "WebFetch", "WebSearch",  # Claude Code 的内建版本
    "ExitPlanMode",
    "ScheduleWakeup",
    "ToolSearch",
]


def render_tool_availability_section(
    tool_names: Iterable[str] | None,
    *,
    lang: str = "en",
) -> str:
    """Render a standalone "# Available Tools" Markdown section.

    Contract (mirrors Claude Code's ``getUsingYourToolsSection`` but stricter):

    1. **Positive enumeration** — list every tool in ``tool_names`` with its
       ``SEARCH_HINT_BY_TOOL`` one-liner. Tools outside the hint map fall
       through with "(no description registered)".
    2. **Negative enumeration** — explicit "You do NOT have X, Y, Z" over the
       confabulation-prone names. Deleted from the list if the actual tool is
       enabled (e.g. ``web_search`` is enabled → strip ``WebSearch`` from the
       negative list, since the model would rightly get confused).
    3. **Meta-question handling rule** — tell the model to enumerate ONLY the
       tools listed above when asked about its own capabilities.

    Args:
        tool_names: the effective tool name list for THIS session. ``None`` or
            empty means "no session-level whitelist"; we render all registered
            tools in that case.
        lang: ``"en"`` (default) or ``"zh"`` — picks headline / prose lang,
            the tool names themselves stay English.
    """
    from ..registry import ALL_TOOLS

    raw_names = list(tool_names) if tool_names else list(ALL_TOOLS.keys())
    # Preserve order; dedupe while preserving order
    seen: set[str] = set()
    ordered_names: list[str] = []
    for n in raw_names:
        if n in seen:
            continue
        seen.add(n)
        ordered_names.append(n)

    # v0.22 — when ``spawn_subagent`` is in the toolset, follow up the
    # one-liner with an enumeration of which named subagents are
    # reachable and what they're each good for. Without this, the agent
    # reads "delegate a focused task" and never connects "delegation"
    # to concrete write capabilities like kb_create / doc_ingest_attachment.
    # This is the prompt-side complement to the runtime ``allowed_subagent_types``
    # gate; we surface what the agent CAN reach so it actually uses it.
    spawn_subagent_extras: list[str] = []
    if names.SPAWN_SUBAGENT in ordered_names:
        try:
            from ..definitions import list_definitions
            for d in list_definitions(kind="subagent"):
                # Short capability summary derived from the definition's
                # tool list — keep it tight to avoid bloat. The full
                # tool surface is loaded inside the child runtime.
                tools = list(d.tools or [])
                # Highlight the WRITE tools (the ones the agent doesn't
                # have directly), since that's the gap the supervisor
                # prompt usually fails to bridge.
                write_tools = [
                    t for t in tools
                    if t.startswith(("kb_", "doc_")) and t != names.KB_STATS
                ]
                cap_hint = (
                    f"writes via {', '.join(f'`{t}`' for t in write_tools[:6])}"
                    if write_tools
                    else f"reads + reflection via {', '.join(f'`{t}`' for t in tools[:6])}"
                )
                # Truncate description at sentence boundary when possible
                # so we don't end with awkward "...retrieves :" fragments.
                desc = (d.description or d.name).strip()
                if len(desc) > 80:
                    cut = desc[:80].rsplit(".", 1)[0] or desc[:80].rsplit(",", 1)[0] or desc[:80]
                    desc = cut.strip().rstrip(":") + "…"
                spawn_subagent_extras.append(
                    f"  - `subagent_type='{d.name}'` — {desc} ({cap_hint})"
                )
        except Exception:
            # Fall through silently — the base tool line still renders.
            pass

    positive_lines: list[str] = []
    for n in ordered_names:
        hint = SEARCH_HINT_BY_TOOL.get(n, "(no description registered)")
        positive_lines.append(f"- `{n}` — {hint}")
        if n == names.SPAWN_SUBAGENT and spawn_subagent_extras:
            positive_lines.extend(spawn_subagent_extras)

    # Negative-enumeration masking: any confabulated name that matches an
    # actually-enabled tool (case-insensitive, underscore/space insensitive)
    # is stripped so we don't send contradictory signals to the model.
    enabled_normalized = {n.replace("_", "").lower() for n in ordered_names}

    has_web = any(
        n in enabled_normalized for n in ("websearch", "webfetch")
    )

    tool_count = len(ordered_names)

    # 动态生成"其它产品的工具例子"——剥掉已启用的同名工具，避免给模型
    # 自相矛盾的信号（例如启用了 web_search 还说 WebSearch 不存在）。
    example_others = [
        t
        for t in (
            "Gmail", "Google Drive", "Google Calendar",
            "Bash", "Shell", "LSP", "Skill", "SlashCommand",
            "Read", "Write", "Edit", "Agent", "Task",
            "WebSearch", "WebFetch", "ScheduleWakeup",
        )
        if t.replace(" ", "").replace("_", "").lower()
        not in enabled_normalized
    ]
    example_tokens_en = ", ".join(f"`{t}`" for t in example_others)
    example_tokens_zh = " / ".join(f"`{t}`" for t in example_others)

    if lang == "zh":
        web_line_zh = (
            "- 本会话**已启用联网工具**（`web_search` / `web_fetch`），被问到"
            "能否联网时，直接回答「可以」并遵守使用约束（先查 KB，不在 "
            "[N] 脚注里引用外链）。"
            if has_web
            else "- 本会话**未启用联网工具**。被问「能联网吗」「能搜索最新信息吗」"
            "时，明确回答「本会话未启用联网工具」，不要假装自己能联网。"
        )
        return (
            "# 可用工具（严格列表）\n\n"
            f"本会话**只有**以下 {tool_count} 个工具，**没有其它任何工具**。"
            "这个列表是穷尽的——不存在「其它工具」「额外能力」「附加插件」等概念。\n\n"
            + "\n".join(positive_lines)
            + "\n\n"
            "# 关于工具枚举的严格约束\n\n"
            f"- 被问「你有什么工具」时，**只**列出上面 {tool_count} 个工具，然后**结束**。"
            "绝不以「其它工具」「另外」「还有」「此外」「**补充**」等过渡词追加任何内容。\n"
            "- 其它 AI 产品（Claude Code CLI、Claude Desktop、Claude API 的 MCP "
            "catalog、Claude.ai 客户端、IDE 插件等）里有的工具（如 "
            f"{example_tokens_zh} 等）在**这里都不存在**。\n"
            "- 禁止举「比喻」——例如说「类似 LSP 的代码智能」「等价于 Skill 的内置」，"
            "这些也会被视为错误暗示自己拥有这些工具。\n\n"
            "# 元问题处理（用户问你的能力 / 工具 / 是否能联网）\n\n"
            f"- 被问「你有什么工具」「你能做什么」时，**只**基于上面 {tool_count} 个工具回答。\n"
            f"{web_line_zh}\n"
            "- 若用户要求「启动 X 工具」/「开启 Y」，告诉他「工具集由会话配置决定，"
            "可在新建会话或会话设置里切换模板（例如『研究/尽调』模板带联网工具）」。"
        )

    web_line_en = (
        "- This session HAS web tools enabled (`web_search` / `web_fetch`). "
        "If asked about web access, answer yes and follow the tool rules "
        "(KB first; web URLs cite inline, not with [N] markers)."
        if has_web
        else "- Web access is NOT enabled on this session. If asked \"can you "
        "browse the web?\" / \"can you search the web?\", say web access is "
        "not available here. Do not pretend to have `web_search` or Bing/Google."
    )
    return (
        "# Available Tools (exhaustive list)\n\n"
        f"This session has EXACTLY {tool_count} tools. There are NO other tools — "
        "no \"other capabilities\", no \"additional plugins\", no \"extra\" tools. "
        "The list below is complete and closed.\n\n"
        + "\n".join(positive_lines)
        + "\n\n"
        "# Strict constraints on tool enumeration\n\n"
        f"- When asked \"what tools do you have?\", list EXACTLY those {tool_count} "
        "tools and STOP. Never append \"other tools\", \"also\", \"additionally\", "
        "\"in addition\", \"plus\", or any transition followed by tools not in "
        "the list above.\n"
        "- Tools present in other AI products (Claude Code CLI, Claude "
        "Desktop, Anthropic MCP catalog, IDE plugins, etc.) — e.g. "
        f"{example_tokens_en} — do NOT exist here.\n"
        "- Do not offer analogies either — phrases like \"similar to LSP\", "
        "\"equivalent to Skill\", \"like a built-in Bash\" will be read as "
        "false claims of having those tools. Stay grounded in the exact list.\n\n"
        "# Meta-question handling (capabilities / tools / web access)\n\n"
        f"- When asked about capabilities, answer ONLY from the {tool_count} "
        "tools listed above.\n"
        f"{web_line_en}\n"
        "- If the user asks to \"enable\" or \"turn on\" a tool, explain that "
        "the tool set is configured per session — either pick a different "
        "template at session creation (the \"Research / DD\" template ships "
        "with web tools), or ask an admin to update the session."
    )


# ──────────────────────────────  Supervisor  ──────────────────────────────


_SUPERVISOR_SKELETON = """\
# Role

{role_line}

# Domain context

{domain_context}

# Hard constraints (must / must-not)

{hard_constraints}

# Workflow (how to approach any user request)

1. Classify the request: **read / understand**, **observe / summarize / write a note**, **execute a change**, **plan decision follow-up**, or **ambiguous**.
2. For *read / understand* → answer directly using retrieval tools. Do not delegate for simple factual Q&A.
3. For *observe / summarize / write a note* → spawn `sub_librarian`. Pass the specific task description; librarian will audit, write, and report.
4. For *execute a change* → spawn `sub_archivist`. If the change touches more than 3 documents or crosses knowledge bases, the archivist must submit a plan first.
5. For *plan decision follow-up* — the current user message starts with `[plan system]` — obey the directive verbatim. Approval means "spawn sub_archivist to execute the stored plan via `get_pending_plan`". Rejection means "acknowledge and stop". Request-changes means "spawn sub_archivist with the user's revision note so it can re-submit_plan". These directives **override** any other classification; do not treat them as out-of-domain even if the original user wording referenced something outside your usual scope.
6. For *ambiguous* → follow the clarify-vs-act decision tree below. Never guess destructive intent.

# Delegation rules (do NOT re-delegate)

- One user request → at most ONE subagent of each type. Spawning the same subagent twice for the same request wastes budget; avoid it.
- Do NOT call a write tool directly. You do not have `doc_tag`, `doc_archive`, `doc_create_note`, etc. in your toolbelt; you reach them via `spawn_subagent(subagent_type=...)`.
- Pass complete briefs when spawning: the subagent cannot ask you for clarification mid-run.
- When a subagent returns, summarize its findings for the user in your own words. Do not verbatim-dump its output.

# Clarify-vs-act decision tree

- Destructive or cross-KB change + ambiguous target → call `ask_user_question` BEFORE spawning `sub_archivist`.
- Batch of 5+ documents with clear intent → spawn `sub_archivist` and let it `submit_plan` for approval.
- Single-document change with unambiguous target → spawn `sub_archivist` directly (archivist may still submit a plan internally).
- Pure read or audit → never clarify; answer directly.

# Tool usage rules

- `rag_retrieve(query, top_n=8)` — go-to for any factual answer. Prefer short keyword-style queries over long sentences.
- `rag_list_docs(keywords?)` — when the user asks "what documents are in this KB" or you need to locate a specific file.
- `rag_read_doc(doc_id)` — when `rag_retrieve` returns chunks but you need the full text to answer with confidence.
- `rag_graph_query(entity)` — only when the KB has a GraphRAG index; otherwise returns empty.
- `kb_stats(kb_id)` — use at the START of a turn if user asks "is this KB healthy?" type questions. Very fast. Do NOT run full `kb_audit` yourself; delegate to librarian.
- `spawn_subagent(subagent_type, description, prompt)` — see Delegation rules above.
- `ask_user_question(question, header, options)` — structured 2-4 choice clarifier. Prefer this over free-text questions when the user's answer is a pick from a small set.
- `submit_plan(title, steps, risk_level, ...)` — use when you yourself are about to take several read steps, and the user benefits from seeing the plan first. For write plans, let the archivist submit.

# Tool cost hints

{tool_annotations}

# Output format

{output_rules}

{domain_extras}\
"""


def build_supervisor_prompt(
    *,
    role_line: str,
    domain_context: str = "",
    hard_constraints: Iterable[str] | None = None,
    output_rules: Iterable[str] | None = None,
    domain_extras: str = "",
    tool_names_for_annotations: Iterable[str] | None = None,
) -> str:
    """Assemble a full supervisor system prompt.

    `role_line` is a single sentence identifying the agent ("You are the
    Shenzhen Affordable Housing Policy Advisor."). `domain_context` gives a
    short paragraph of domain background. `hard_constraints` adds domain-
    specific forbidden behaviors on top of the baseline STRICT_RAG_CONSTRAINTS.
    `output_rules` replaces baseline RETRIEVAL_OUTPUT_RULES when you need
    different behavior (e.g. agents that must not emit [N] citations).
    `domain_extras` is raw markdown appended at the end.
    `tool_names_for_annotations` (Phase 2.6 v0.4) restricts the cost-hint
    table to specific tool names; ``None`` lists every registered tool.
    """
    hc = list(STRICT_RAG_CONSTRAINTS) + list(hard_constraints or [])
    hard = "\n".join(f"- {line}" for line in hc)
    outr = output_rules if output_rules is not None else RETRIEVAL_OUTPUT_RULES
    out = "\n".join(f"- {line}" for line in outr)
    domain_ctx = domain_context.strip() or "General knowledge base."
    from ..annotations import annotations_summary_for_prompt

    ann = annotations_summary_for_prompt(
        list(tool_names_for_annotations) if tool_names_for_annotations is not None else None
    )
    return _SUPERVISOR_SKELETON.format(
        role_line=role_line.strip(),
        domain_context=domain_ctx,
        hard_constraints=hard,
        tool_annotations=ann or "(no annotated tools registered)",
        output_rules=out,
        domain_extras=("\n" + domain_extras.strip() + "\n") if domain_extras else "",
    )


# ──────────────────────────────  Subagent  ──────────────────────────────


_SUBAGENT_SKELETON = """\
# Role

{role_line}

# Mission

{mission}

# Hard constraints (must / must-not)

{hard_rules}

# Workflow

{workflow_steps}

# Tool usage rules

{tool_rules}

# Tool cost hints

{tool_annotations}

# Output format

{output_rules}
"""


def build_subagent_prompt(
    *,
    role_line: str,
    mission: str,
    hard_rules: Iterable[str],
    workflow_steps: Iterable[str],
    tool_rules: Iterable[str],
    output_rules: Iterable[str] | None = None,
    tool_names_for_annotations: Iterable[str] | None = None,
) -> str:
    """Assemble a subagent system prompt.

    Subagent prompts are tighter than supervisor prompts — one mental mode, no
    delegation/clarification branches unless the subagent explicitly owns
    that responsibility (e.g., sub_archivist still uses submit_plan).
    """
    hr = "\n".join(f"- {line}" for line in hard_rules)
    ws = "\n".join(f"{i}. {s}" for i, s in enumerate(workflow_steps, 1))
    tr = "\n".join(f"- {line}" for line in tool_rules)
    outr = output_rules if output_rules is not None else RETRIEVAL_OUTPUT_RULES
    out = "\n".join(f"- {line}" for line in outr)
    from ..annotations import annotations_summary_for_prompt

    ann = annotations_summary_for_prompt(
        list(tool_names_for_annotations) if tool_names_for_annotations is not None else None
    )
    return _SUBAGENT_SKELETON.format(
        role_line=role_line.strip(),
        mission=mission.strip(),
        hard_rules=hr,
        workflow_steps=ws,
        tool_rules=tr,
        tool_annotations=ann or "(no annotated tools registered)",
        output_rules=out,
    )


# ──────────────────────────────  Tool descriptions  ──────────────────────────────


def build_tool_description(
    *,
    when: str,
    what: str = "",
    usage_notes: Iterable[str] | None = None,
    examples: Iterable[str] | None = None,
    related: Iterable[str] | None = None,
    max_chars: int = 900,
) -> str:
    """Claude-Code-style tool description — English, concise, structured.

    Four optional blocks:
        Use this tool when <when>.
        <what>              [one sentence capability]
        Usage notes:        [2-5 bullets of operational constraints]
        Examples:           [1-3 "...X... → call rag_retrieve" scenarios]
        Related tools:      [cross-reference bullet list]

    The `when` field is the most important — it drives the model's tool
    selection decision. Keep it specific: a situation, not a tool category.

    `max_chars` guards description length. Claude Code's FileReadTool is ~80
    tokens; our richer semantic tools need a bit more, but 900 chars is a
    hard ceiling (~250 tokens). If you exceed, prune or move to system
    prompt.
    """
    parts: list[str] = [f"Use this tool when {when.strip().rstrip('.')}."]
    if what.strip():
        parts.append(what.strip().rstrip("."))
    if usage_notes:
        parts.append("Usage notes:\n" + "\n".join(f"- {n}" for n in usage_notes))
    if examples:
        parts.append("Examples:\n" + "\n".join(f"- {e}" for e in examples))
    if related:
        parts.append("Related:\n" + "\n".join(f"- {r}" for r in related))
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        # Soft warning via exception — catches bloat early during dev
        raise ValueError(
            f"Tool description exceeds {max_chars} chars (got {len(text)}); "
            "prune or move detail to the agent's system prompt."
        )
    return text
