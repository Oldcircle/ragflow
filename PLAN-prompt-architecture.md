# Phase 2.8 — Prompt 系统架构重写

> **定位**：把 v0.3 立的"形式上 8 段式 prompt"升级到 claude-code-ref 的真正
> 设计——**节级缓存 + 静态/动态分界 + 工具名常量 + 共享段库 + enabledTools
> 过滤 + 数字化长度锚点**。
> **参考**：`vendor/claude-code-ref/src/utils/systemPrompt.ts` +
> `src/constants/prompts.ts` + `src/constants/systemPromptSections.ts` +
> `packages/builtin-tools/src/tools/AgentTool/built-in/exploreAgent.ts`。
> **起始日期**：2026-04-25

---

## 一、动机

`AUDIT-claude-code-alignment.md` §五-e 列出 6 个偏差（D1–D6）。一句话总结：
**v0.3 抄了节标题没抄到机制**。当前 prompt 系统里：

- 每轮整 prompt 重建，DeepSeek prompt_cache 命中率 ~0%
- 工具名 50+ 处裸字面量散落
- Hard rules / Workflow / Tool rules 三段重叠 ~30%
- 共享段（READ-ONLY 禁令、PLAN_GATE 约束等）在 4 个 subagent 文件里各写一份
- 没有数字化长度锚点

实测 sub_archivist.py 192 行 = ~3.4K 字符 prompt，对标 claude-code-ref
exploreAgent.ts 56 行 = ~1.8K 字符，**信息密度差 2-3 倍**。

---

## 二、设计原则（按优先级）

1. **段是一等公民**。每段 = `PromptSection(name, compute, cache_break, reason)`。
   组装由数组拼接完成，不再用大字符串模板。
2. **静态/动态明确切分**。一条 `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` 标记把 prompt
   分成"会话内永不变"和"每轮可能变"。前者跨轮 memoize，后者每轮重算。
3. **工具名只能从 `_names.py` 取**。所有 prompt / 段 / 测试 import 常量；
   重命名 = 改一个文件。
4. **共享段库为重用第一**。`READ_ONLY_BLOCK` / `PLAN_GATE_BLOCK` /
   `CITATION_RULES` 等以"块"为单位被多个 subagent 引用。
5. **`enabled_tools` 是段函数的标准入参**。段决定"我提到的工具是否启用"，
   不启用就 return None 自动消失。
6. **不重复信息**。一份信息只能进 prompt 一次：
   - 工具用法 → tool description（已有 `[intent]` 前缀 + MCP annotations）
   - 工具间偏好（"用 doc_archive 而非 doc_upload_from_url"）→ system prompt 段
   - 不要再有第三处"Tool cost hints"表
7. **数字化优先**。"keep ≤25 words" 比 "be concise" 有效（claude-code-ref
   `prompts.ts:538` 的实测结论）。

---

## 三、参考映射（精确到行号）

| 我们要做的 | claude-code-ref 对应 | 抄什么 | 不抄什么 |
|---|---|---|---|
| `PromptSection` 抽象 | `src/constants/systemPromptSections.ts:8-58` | `name / compute / cacheBreak` 三字段；`resolveSystemPromptSections` memoize 逻辑；`DANGEROUS_uncachedSystemPromptSection` 强制带 reason | Bun 全局 `bootstrap/state.ts` 单例；analytics logEvent |
| 静态/动态分界 | `src/constants/prompts.ts:116-117` + `:577` | `SYSTEM_PROMPT_DYNAMIC_BOUNDARY = '__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__'` 字面量 + 在 prompt 数组里插入位置；`shouldUseGlobalCacheScope()` 模式 | `feature('PROMPT_CACHE_BREAK_DETECTION')` 检测——我们用 DeepSeek 不需要 |
| 段函数签名 | `src/constants/prompts.ts:188-199` (`getSimpleSystemSection`) / `:271-316` (`getUsingYourToolsSection(enabledTools)`) | `enabledTools: Set<string>` + `prependBullets(items)` 渲染 | `feature()` 多构建变体 / `process.env.USER_TYPE` 商业版分叉 |
| 节级条件渲染 | `src/constants/prompts.ts:495-559` | `dynamicSections` 数组里用 `...(condition ? [section] : [])` 控制是否纳入；`feature('TOKEN_BUDGET')` 例 | KAIROS / PROACTIVE / COORDINATOR 商业 feature flag |
| 工具名常量 | `packages/builtin-tools/src/tools/*/toolName.ts` 或 `*/constants.ts`（`BASH_TOOL_NAME` / `FILE_READ_TOOL_NAME` 等约 20 个） | 一工具一常量；prompt 用 f-string `${BASH_TOOL_NAME}` 拼 | 每个工具独立模块——我们集中到 `_names.py` 一个文件即可 |
| 共享段（READ-ONLY） | `built-in/exploreAgent.ts:25-37` 的 `=== CRITICAL: READ-ONLY MODE ===` block；`planAgent.ts:23-34` 同款重用 | 全大写标题 + 5 行 PROHIBITED 列表 + "you do NOT have access to ..." 收尾的格式 | embedded vs not 双分支（我们没这个分歧） |
| 数字化长度锚点 | `src/constants/prompts.ts:535-541` | "Length limits: keep text between tool calls to ≤25 words. Keep final responses to ≤100 words..." 措辞 | USER_TYPE 'ant' gating |
| Tone / style 段 | `src/constants/prompts.ts:434-446` | 5 条 bullet 风格 (emoji / `file_path:line_number` / "Do not use a colon before tool calls") | github owner/repo#123 link 形式 |

**注意**：所有引用都是"**学模式不抄代码**"。我们 Python 实现，不会 import
任何 ref 的 .ts 文件。

---

## 四、模块布局

```
api/agent_v2/
├── tools/
│   └── _names.py                         # NEW: 工具名常量集中
├── prompting/
│   ├── __init__.py                       # 调整 export
│   ├── builder.py                        # REFACTOR: PromptSection + cache + boundary
│   └── sections.py                       # NEW: 共享段库
└── definitions/
    └── built_in/
        ├── _common.py                    # REFACTOR: SUPERVISOR_TOOLS 切到 _names
        ├── sub_archivist.py              # REFACTOR: ~190 行 → ~80 行
        ├── sub_librarian.py              # REFACTOR
        ├── sub_evidence_checker.py       # REFACTOR
        ├── sub_policy_researcher.py      # REFACTOR
        ├── supervisor_baozhang.py        # REFACTOR
        ├── supervisor_generic_policy.py  # REFACTOR
        ├── supervisor_customer_support.py # REFACTOR
        ├── supervisor_internal_wiki.py   # REFACTOR
        ├── supervisor_legal_contract.py  # REFACTOR
        └── supervisor_research.py        # REFACTOR
```

---

## 五、核心 API 设计

### 5.1 `tools/_names.py`

```python
"""Single source of truth for tool name strings.

All prompt-rendering code, registry, and tests must import constants from
here rather than using bare string literals. Renaming a tool then flows
through one file.
"""

# Read
RAG_RETRIEVE = "rag_retrieve"
RAG_LIST_DOCS = "rag_list_docs"
RAG_READ_DOC = "rag_read_doc"
RAG_GRAPH_QUERY = "rag_graph_query"

# Delegation / interaction
SPAWN_SUBAGENT = "spawn_subagent"
ASK_USER_QUESTION = "ask_user_question"
SUBMIT_PLAN = "submit_plan"
GET_PENDING_PLAN = "get_pending_plan"

# Reflect
KB_STATS = "kb_stats"
KB_AUDIT = "kb_audit"
DOC_LIST_RECENT_CHANGES = "doc_list_recent_changes"

# Write — destructive / state-changing
DOC_TAG = "doc_tag"
DOC_RENAME = "doc_rename"
DOC_ARCHIVE = "doc_archive"
DOC_REPARSE = "doc_reparse"
DOC_UPLOAD_FROM_URL = "doc_upload_from_url"
KB_CREATE = "kb_create"
DOC_CREATE_NOTE = "doc_create_note"

# Web
WEB_SEARCH = "web_search"
WEB_FETCH = "web_fetch"

# Attachments
WEB_FETCH_TO_ATTACHMENT = "web_fetch_to_attachment"
DOC_INGEST_ATTACHMENT = "doc_ingest_attachment"

# Convenience tuples for common subsets
ALL_READ_TOOLS: tuple[str, ...] = (
    RAG_RETRIEVE, RAG_LIST_DOCS, RAG_READ_DOC, RAG_GRAPH_QUERY,
)
ALL_WRITE_TOOLS: tuple[str, ...] = (
    DOC_TAG, DOC_RENAME, DOC_ARCHIVE, DOC_REPARSE,
    DOC_UPLOAD_FROM_URL, KB_CREATE, DOC_CREATE_NOTE,
    DOC_INGEST_ATTACHMENT,
)
```

### 5.2 `prompting/builder.py` 重写

```python
@dataclass
class PromptSection:
    """A named, optionally-cacheable prompt section.

    Mirrors claude-code-ref's `systemPromptSection` (constants/
    systemPromptSections.ts:20). The `compute` callback receives
    `(enabled_tools, ctx)` and returns the rendered string or None to
    drop the section entirely.

    `cache_break=True` requires a non-empty `reason` — borrowed from
    `DANGEROUS_uncachedSystemPromptSection` to force authors to justify
    cache busts.
    """
    name: str
    compute: Callable[[set[str], "PromptCtx"], str | None]
    cache_break: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if self.cache_break and not self.reason:
            raise ValueError(
                f"PromptSection({self.name!r}, cache_break=True) requires "
                "a `reason` explaining why cache-busting is necessary."
            )


@dataclass
class PromptCtx:
    """Context passed to every section's compute callback."""
    role_line: str
    enabled_tools: set[str]
    domain_context: str = ""
    domain_extras: str = ""
    pending_plan_status: str | None = None
    kb_ids: tuple[str, ...] = ()
    lang: str = "en"


SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"


class PromptCache:
    """Per-runner cache for memoized PromptSection outputs.

    Cleared on session boundary (compactor / new session). Subagents get
    a fresh cache; not shared across subagent boundaries because their
    `enabled_tools` differs.
    """
    def __init__(self) -> None:
        self._store: dict[str, str | None] = {}

    def resolve(
        self, sections: Sequence[PromptSection], ctx: PromptCtx,
    ) -> list[str]:
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


def assemble_prompt(
    static_sections: Sequence[PromptSection],
    dynamic_sections: Sequence[PromptSection],
    ctx: PromptCtx,
    cache: PromptCache | None = None,
) -> str:
    """Assemble the full system prompt.

    Layout:
        [static_sections joined]
        __SYSTEM_PROMPT_DYNAMIC_BOUNDARY__
        [dynamic_sections joined]
    """
    cache = cache or PromptCache()
    parts: list[str] = []
    parts.extend(cache.resolve(static_sections, ctx))
    parts.append(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    parts.extend(cache.resolve(dynamic_sections, ctx))
    return "\n\n".join(parts)


# Backward-compat builders — keep until all definitions migrated
def build_supervisor_prompt(*, role_line, ...) -> str: ...
def build_subagent_prompt(*, role_line, ...) -> str: ...
```

**Backward compat policy**：旧 `build_supervisor_prompt` / `build_subagent_prompt`
保留**一个版本**，内部走 `assemble_prompt(...)`，旧 caller 不必改。下个 phase
（v0.10+）才删除旧函数。

### 5.3 `prompting/sections.py` 共享段库

每个段就是一个工厂函数返 `PromptSection`。命名约定：`make_xxx_section()`。

```python
def make_identity_section(role_line: str) -> PromptSection:
    """Static — agent identity. Never cache-busts within a session."""
    return PromptSection(
        name="identity",
        compute=lambda _tools, ctx: f"# Role\n\n{ctx.role_line.strip()}",
    )


def make_read_only_block() -> PromptSection:
    """Static — used by sub_evidence_checker, sub_policy_researcher,
    sub_librarian (the read-flavored subagents).

    Mirrors claude-code-ref/built-in/exploreAgent.ts:25-37.
    """
    return PromptSection(
        name="read_only_block",
        compute=lambda _tools, _ctx: """\
=== CRITICAL: READ-ONLY MODE ===

This is a READ-ONLY task. You are STRICTLY PROHIBITED from:
- Calling any tool that mutates KB state (doc_tag, doc_rename,
  doc_archive, doc_reparse, doc_upload_from_url, kb_create,
  doc_ingest_attachment, doc_create_note)
- Spawning subagents (you cannot delegate further)
- Submitting a plan (only the supervisor can plan)

Your role is EXCLUSIVELY to read, analyze, and report. Attempting to
call a write tool will fail at the runtime gate.""",
    )


def make_plan_gate_block() -> PromptSection:
    """Static — used by sub_archivist + supervisor (those who can
    legitimately reach submit_plan).

    Folds three previously-duplicated rules into one block:
      1. Hard rule: "must submit_plan before batch ≥3"
      2. Workflow step: "if multi-doc, submit_plan first"
      3. Tool rule: "submit_plan stops the turn"
    """
    return PromptSection(
        name="plan_gate_block",
        compute=lambda tools, _ctx: (
            None if names.SUBMIT_PLAN not in tools else """\
# Plan gate (runtime-enforced)

Before any batch of ≥3 state-changing operations, OR any cross-KB move,
OR any external URL ingest, you MUST call submit_plan first and STOP.
The runtime rejects write tools after submit_plan in the same turn,
and while session.pending_plan_status is waiting / rejected /
request_changes. The gate lifts only when the next user message starts
with [plan approved]."""),
    )


def make_citation_rules_section(
    enforce_level: str,
    numeric_strict: bool,
) -> PromptSection:
    """Static — citation [N] rules. None for operators (citation_enforce='off')."""
    if enforce_level == "off":
        return PromptSection(
            name="citation_rules",
            compute=lambda _t, _c: None,
        )
    return PromptSection(
        name="citation_rules",
        compute=lambda _t, _c: """\
# Citation format

- Annotate every factual sentence with [N], where N matches the chunk
  order from rag_retrieve / rag_read_doc this turn.
- Do NOT inline file names — the frontend renders Sources from the [N]
  ids automatically.
- Do NOT annotate hedging, opinion, or summary sentences.""",
    )


def make_tone_and_style_section() -> PromptSection:
    """Static — mirrors claude-code-ref/prompts.ts:434-446."""
    return PromptSection(
        name="tone_and_style",
        compute=lambda _t, _c: """\
# Tone and style

- Only use emojis if the user explicitly requests it.
- Keep responses short and concise.
- When referencing a document, use the form `<doc_name>:chunk-<N>` so
  the frontend can deep-link.
- Do not use a colon before tool calls. Text like "Let me search:" then
  a tool call should just be "Let me search." with a period.""",
    )


def make_numeric_length_anchors_section() -> PromptSection:
    """Static — mirrors claude-code-ref/prompts.ts:535-541."""
    return PromptSection(
        name="numeric_length_anchors",
        compute=lambda _t, _c: """\
# Length limits

- Keep text between tool calls to ≤25 words.
- Keep final responses to ≤100 words unless the task requires more
  detail.
- For [step K/N done] markers in operator subagents, keep each line
  ≤30 words.""",
    )


def make_pending_plan_section() -> PromptSection:
    """Dynamic — surfaces the current plan_status. Cache-busts on change.

    Mirrors claude-code-ref's MCP_INSTRUCTIONS dynamic section pattern.
    """
    return PromptSection(
        name="pending_plan",
        cache_break=True,
        reason="pending_plan_status changes per turn after a plan is submitted",
        compute=lambda _t, ctx: (
            None if not ctx.pending_plan_status
            else f"# Pending plan\n\nstatus: {ctx.pending_plan_status}"
        ),
    )


# ... (more sections: tool_availability, env_info, hard_rag_constraints,
#      delegation_rules, output_rules, etc.)
```

### 5.4 AgentDefinition 改造

```python
# Before: sub_archivist.py — 192 行
ARCHIVIST_HARD_RULES = [...]   # 7 条
ARCHIVIST_WORKFLOW = [...]     # 7 步
ARCHIVIST_TOOL_RULES = [...]   # 11 条
ARCHIVIST_OUTPUT_RULES = [...] # 5 条
DEFINITION = AgentDefinition(
    system_prompt=build_subagent_prompt(
        role_line=...,
        mission=...,
        hard_rules=ARCHIVIST_HARD_RULES,
        workflow_steps=ARCHIVIST_WORKFLOW,
        tool_rules=ARCHIVIST_TOOL_RULES,
        output_rules=ARCHIVIST_OUTPUT_RULES,
        tool_names_for_annotations=ARCHIVIST_TOOLS,
    ),
    ...
)

# After: sub_archivist.py — ~80 行
from ...prompting.sections import (
    make_identity_section, make_plan_gate_block,
    make_archivist_workflow_section, make_archivist_output_section,
    make_tone_and_style_section, make_numeric_length_anchors_section,
)

ARCHIVIST_TOOLS = [
    names.DOC_TAG, names.DOC_RENAME, names.DOC_ARCHIVE, names.DOC_REPARSE,
    names.DOC_UPLOAD_FROM_URL, names.KB_CREATE,
    names.WEB_FETCH_TO_ATTACHMENT, names.DOC_INGEST_ATTACHMENT,
    names.RAG_LIST_DOCS, names.RAG_READ_DOC,
    names.ASK_USER_QUESTION, names.SUBMIT_PLAN, names.GET_PENDING_PLAN,
]

ARCHIVIST_STATIC_SECTIONS = [
    make_identity_section("You are sub_archivist, the KB ops specialist."),
    make_archivist_mission_section(),
    make_plan_gate_block(),
    make_archivist_workflow_section(),
    make_tone_and_style_section(),
    make_numeric_length_anchors_section(),
    make_archivist_output_section(),
]
ARCHIVIST_DYNAMIC_SECTIONS = [
    make_pending_plan_section(),
]

DEFINITION = AgentDefinition(
    name="sub_archivist", version="2.0.0",  # major bump for prompt rewrite
    description=(...),  # unchanged
    when_to_use=(...),  # unchanged
    kind="subagent",
    system_prompt=lambda ctx: assemble_prompt(
        static_sections=ARCHIVIST_STATIC_SECTIONS,
        dynamic_sections=ARCHIVIST_DYNAMIC_SECTIONS,
        ctx=ctx,
        cache=ctx.prompt_cache,  # injected by runner
    ),
    tools=ARCHIVIST_TOOLS,
    ...
)
```

注意：`system_prompt` 字段从 `str` 升级到 `str | Callable[[PromptCtx], str]`。
Schema 已支持 callable（`api/agent_v2/definitions/schema.py` 的 `system_prompt:
str | Callable` 注释里写了"支持 callable"），现在我们真用上。

---

## 六、阶段拆解（11–14h 合计）

| Stage | 内容 | 工时 | 关联 task |
|---|---|---:|---|
| **S1** | `tools/_names.py` 创建 + registry / annotations 切换到常量 | 1h | #4 |
| **S2** | `prompting/builder.py` 重写：PromptSection / PromptCtx / PromptCache / assemble_prompt / SYSTEM_PROMPT_DYNAMIC_BOUNDARY；旧 `build_supervisor_prompt` / `build_subagent_prompt` 内部转调新 API（保 backward compat）| 3h | #5 |
| **S3** | `prompting/sections.py` 共享段库：identity / mission / read_only_block / plan_gate_block / citation_rules / tone_and_style / numeric_length_anchors / pending_plan / hard_rag_constraints / delegation_rules / output_rules + tool_availability（替换 `render_tool_availability_section`）| 3h | #6 |
| **S4** | 4 subagent 重构（sub_archivist / sub_librarian / sub_evidence_checker / sub_policy_researcher）| 2h | #7 |
| **S5** | 6 supervisor 重构（baozhang / generic_policy / customer_support / internal_wiki / legal_contract / research）+ `_common.py` 调整 | 1.5h | #8 |
| **S6** | 测试：`test_prompting_sections.py` 新增 + 适配 `test_definitions` / `test_sub_archivist` / `test_tool_availability_prompt` | 2h | #9 + #10 |
| **S7** | 实机 smoke + 测量 prompt 长度 / cache 预期效果 + 文档收尾 | 1h | #11 |
| **S8** | Commit 拆分（4 个独立 commit）| 0.5h | #12 |

---

## 七、风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 重构期间运行时坏掉 | 高 | 旧 `build_supervisor_prompt` / `build_subagent_prompt` 保留；新旧并存到测试全绿才切 |
| `system_prompt` callable 接 runtime ctx 改 schema | 中 | schema 已声明 `Callable` 支持，只是没真用；改 `_resolve_definition_to_runner` 传 PromptCtx |
| MCP server 启动时 prompt 评估时机 | 中 | 老路径是 definition 时刻就把 prompt 字符串生成；新路径是 runner 启动时按 ctx 生成。需要确认 SDK MCP server 注册不依赖 prompt 内容 |
| 测试断言 substring 大量失败 | 中 | 重构 assert 风格：从 `"submit_plan" in prompt` 改成 `"plan_gate_block" in section_names`，关心结构不关心字面 |
| live 行为回归（A1 / A2 / A3 模型选错工具）| 中 | S7 跑 live smoke 实测；保留可回退的 `build_*_prompt` 旧入口 |

---

## 八、不做（明确推迟）

- ❌ **真正接通 prompt cache** 到 SDK 的 cache_control / DeepSeek prompt_cache：
  本批只做架构层切分（前缀稳定），缓存接入留 v0.15
- ❌ **OutputStyle 配置 / 多语言**：v0.3 决策 prompt 强制英文，不重新引入选项
- ❌ **`feature()` flag 化段**：claude-code-ref 用 `feature('TOKEN_BUDGET')` /
  `feature('PROACTIVE')` 控制段，我们没有 feature flag 系统，先用 if/else
  + AgentDefinition 字段做条件
- ❌ **Coordinator mode / Proactive mode prompt 路径**：那是 claude-code-ref
  的多 agent 编排，跟 KB Agent 场景不直接对应

---

## 九、成功标准

1. **行为不退化**：A1 / A2 / A3 live 场景与 v0.13 表现一致或更好（测量
   subagent 数 / cost / 工具选择正确性）
2. **数字达标**：
   - sub_archivist.py ≤ 90 行
   - 渲染后 sub_archivist prompt ≤ 2.2K 字符
   - supervisor 平均 ≤ 30 行
3. **测试不挂**：现有 478 测试全绿；新增 ≥20 case 覆盖 PromptSection /
   PromptCache / sections.py
4. **Ruff 不挂**
5. **`grep -rE "\"(submit_plan|doc_tag|doc_archive|doc_rename|kb_create|...)\""
   api/agent_v2/`** 命中数从 50+ 降到 0（除 `_names.py` 内部）

---

## 十、版本记录

| 日期 | 版本 | 决策 |
|---|---|---|
| 2026-04-25 | v0.1 | 立项；6 个偏差（D1–D6）+ 9 个 task（T19–T27）+ 完整设计 + 8 stage 拆解 |
| 2026-04-25 | v1.0 | 落地：S1–S7 全完成。tools/_names.py（135 行）+ prompting/builder.py 重写 + prompting/sections.py 共享段库（590 行）+ 4 subagent + 6 supervisor 切到 callable system_prompt + new preset。560 passed / 8 skipped / ruff 0 错。sub_archivist prompt -58%。dynamic sections 已写但 supervisor_dynamic_sections() 返 [] 留 v0.15 真启用 |
