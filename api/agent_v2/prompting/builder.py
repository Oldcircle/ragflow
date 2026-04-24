"""Prompt builders — Claude-Code-style 8-section system prompts, English.

## Design overview

Claude Code's system prompts are **section-indexed**, not free-form. Each agent
gets a stable skeleton (Role → Context → Constraints → Workflow → Tools →
Output → Examples → Notes), so the model can navigate instructions like a
table of contents instead of re-parsing free prose every turn.

We split into two builders because KB supervisors and subagents have different
jobs:

- **Supervisor** = "front door" — understands user intent, delegates to
  subagents, never touches write tools directly. Needs the
  *Delegation rules* + *Clarify-vs-act decision tree* sections.
- **Subagent** = "specialist" — single mental mode (read-only investigator /
  destructive archivist / observer-and-writer librarian). Needs tighter *Hard
  rules* and *Output format* sections.

We do **not** port Claude Code's dynamic boundary marker
(`__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`) — that exists for prompt caching on
Anthropic's endpoint, which DeepSeek doesn't match. Can revisit if we switch
providers.

## Section order (supervisor)

1. Role & mission
2. Domain context
3. Hard constraints (must / must-not)
4. Workflow (how to approach a user request)
5. Delegation rules (when to spawn which subagent)
6. Clarify-vs-act decision tree
7. Tool usage rules (per tool: when + how)
8. Output format (citation rules / structured answers)

## Section order (subagent)

1. Role & mission
2. Hard constraints (scope boundaries, refusals)
3. Workflow (step-by-step expected behavior)
4. Tool usage rules
5. Output format

## Philosophy

Prompts are **stable contracts** between designer and model. Each section has
one job; the model can skip to the right section. Avoid:

- Narrative prose across sections
- Redundancy (same rule in two sections)
- Chinese-English mixing (we commit to English as lingua franca)
- Markdown decoration for its own sake (`✅ 🚫 🟡` etc.)

Reference: `/tmp/architectural-synthesis.md` §5; `AUDIT-claude-code-alignment.md` §3.
"""

from __future__ import annotations

from typing import Iterable


# ──────────────────────────────  Shared constants  ──────────────────────────────


STRICT_RAG_CONSTRAINTS: list[str] = [
    "All factual claims (numbers, dates, durations, percentages, monetary amounts) "
    "MUST be supported by retrieved source chunks. Never supplement from training knowledge.",
    "If a required fact is not in the retrieved material, say so explicitly using the "
    "phrase 'no direct basis in the knowledge base' — do not guess, round, or infer.",
    "Never fabricate document names, authority names, section numbers, or dates.",
    "Never generalize a city's policy to a different city, or a version's rule to a "
    "different version.",
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
    "rag_retrieve": "search knowledge base semantically for relevant chunks",
    "rag_list_docs": "list documents in the current knowledge base",
    "rag_read_doc": "read the full text of a single document",
    "rag_graph_query": "query the knowledge graph for an entity",
    # Delegation / interaction
    "spawn_subagent": "delegate a focused task to a subagent",
    "ask_user_question": "ask the user a multiple-choice clarifying question",
    "submit_plan": "submit a plan for user approval before executing a batch",
    # Reflect / observe
    "kb_stats": "get a quick health snapshot of a knowledge base",
    "kb_audit": "run a structured audit of knowledge base health",
    "doc_list_recent_changes": "list recent audit-log entries for this tenant",
    "get_pending_plan": "read back the approved plan to execute step by step",
    # Write
    "doc_create_note": "save agent-authored markdown as a new document",
    "doc_tag": "add, remove, or replace document tags",
    "doc_rename": "rename a document",
    "doc_archive": "move a document to another knowledge base",
    "doc_reparse": "clear chunks and re-run the parser on a document",
    "doc_upload_from_url": "download a url into the knowledge base",
    "kb_create": "create a new empty knowledge base",
    # Web (Phase 2.6 v0.7)
    "web_search": "search the public web for recent or external info",
    "web_fetch": "fetch the full text of a specific web page",
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

    names = list(tool_names) if tool_names else list(ALL_TOOLS.keys())
    # Preserve order; dedupe while preserving order
    seen: set[str] = set()
    ordered_names: list[str] = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        ordered_names.append(n)

    positive_lines: list[str] = []
    for n in ordered_names:
        hint = SEARCH_HINT_BY_TOOL.get(n, "(no description registered)")
        positive_lines.append(f"- `{n}` — {hint}")

    # Build negative list by stripping any confabulated name that matches an
    # actually-enabled tool (case-insensitive, underscore/space insensitive).
    enabled_normalized = {n.replace("_", "").lower() for n in ordered_names}
    negative = [
        name
        for name in _CONFABULATED_TOOLS
        if name.replace("_", "").replace(" ", "").lower() not in enabled_normalized
    ]
    negative_line = ", ".join(f"`{x}`" for x in negative)

    has_web = any(
        n in enabled_normalized for n in ("websearch", "webfetch")
    )

    if lang == "zh":
        web_line_zh = (
            "- 本会话**已启用联网工具**（`web_search` / `web_fetch`），被问到"
            "能否联网时，直接回答「可以」并遵守它们的使用约束（先查 KB，不在 "
            "[N] 脚注里引用外链）。"
            if has_web
            else "- 本会话**未启用联网工具**。被问「能联网吗」「能搜索最新信息吗」"
            "时，明确回答「本会话未启用联网工具」，不要假装自己能联网。"
        )
        return (
            "# 可用工具\n\n"
            "本会话你能调用的工具**仅限**以下列表：\n\n"
            + "\n".join(positive_lines)
            + "\n\n# 你没有的工具\n\n"
            "本会话**没有**以下工具（任何客户端 / 产品线里的同名工具不代表你这里也有）：\n\n"
            f"{negative_line}。\n\n"
            "# 元问题处理（用户问你的能力 / 工具 / 是否能联网）\n\n"
            "- 被问「你有什么工具」「你能做什么」「能联网吗」等元问题时，"
            "**只**根据上面的「可用工具」清单老实回答。\n"
            f"{web_line_zh}\n"
            "- **禁止**凭训练记忆编造自己有但实际没注册的工具。\n"
            "- 若用户说「启动 X 工具」/「开启 Y」，告诉他「工具集由会话配置决定，"
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
        "# Available Tools\n\n"
        "The ONLY tools you can call in this session are listed below. "
        "Do not attempt to invoke anything else.\n\n"
        + "\n".join(positive_lines)
        + "\n\n# Tools you do NOT have\n\n"
        "You do NOT have access to any of the following — they may exist in "
        "other products (Claude Code CLI, Anthropic MCP catalog, etc.) but "
        "they are not registered on this session:\n\n"
        f"{negative_line}.\n\n"
        "# Meta-question handling (capabilities / tools / web access)\n\n"
        "- When the user asks about your capabilities (\"what tools do you "
        "have?\", \"can you browse the web?\", \"can you send email?\", etc.), "
        "enumerate ONLY the tools from the \"Available Tools\" section above. "
        "Never claim tools from memory of other AI products.\n"
        f"{web_line_en}\n"
        "- Never invent Gmail, Drive, Bash, LSP, Skill, or any tool not "
        "listed above.\n"
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
