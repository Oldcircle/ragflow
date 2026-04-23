# Phase 2.5 — Agent Runtime 成熟化

> **定位**：把 Agent v2 从"能演示的工作台"推到"可持续运营的产品内核"。不做通用 agent runtime（claude-code-ref 那种），做**企业知识库 Agent 的成熟化**。
> **参考**：全程对标 `~/Opensource/vendor/claude-code-ref/`，但**只抄适配我们场景的部分**。
> **起始日期**：2026-04-23

---

## 一、为什么要做这个 Phase

Phase 2 结束时，Agent v2 能力清单看起来已经齐了：RAG 工具 + RBAC + 飞书渠道 + subagent + 配额。但**外部评审指出三个结构性缺口**，决定能否从"内部 demo"走到"能交付给付费客户"：

### 缺口 1：答案可信度没有硬保障

企业知识库 Agent 的生死线是"答案里的数字 / 金额 / 年限 / 日期必须来自 chunk"。当前链路：

- LLM 调 `rag_retrieve` → 拿回 chunks → 自行生成 Markdown + `[N]` 脚注
- **没有任何后置校验**：LLM 可以编一个数字，只要它在后面随手标个 `[1]`，前端就照样渲染脚注链接
- 保障房 10 题评测是靠**人工打分**兜底的（M1.5），没法自动化

这是"演示能跑 / 企业不敢买"的典型特征。

### 缺口 2：Session 在 UI 上有，模型侧是无状态 QA

- `agent_v2_app.py:371` 每轮新建 `AgentRunner` 只传当前 `user_message`
- `runner.py:180` 进到 `query(prompt=user_message, options=options)` — SDK 看不到历史
- `AgentV2MessageService.list_by_session()` 把消息落库了，但**从未读出来**喂给模型

后果：追问"刚才那个政策里的年龄限制呢？"、"把第二条展开说说"必崩。用户会自己学会每次都把上下文粘一遍，这等于承认"这个 Agent 没记忆"。

### 缺口 3：Agent / Tool 定义是硬编码

- `registry.py` 把 5 个工具写死成 `ALL_TOOLS` dict
- Agent 的 system prompt / max_turns / budget 散落在 DB 表的列 + 模板文件
- 加新 Agent 类型（"保障房顾问 / 合同分析 / 研报综合"）只能套模板，没法像 claude-code-ref 一样**声明式定义一个 agent 并在 subagent 路径里按 `subagent_type` 选**

P2.3 已经做了通用 `spawn_subagent`。要让它真正用起来，需要"命名 subagent"机制，这是 Agent Definition manifest 的直接收益。

---

## 二、优先级（关键）

外部评审原文把"多轮上下文"放第一优先。**我们不同意**：对企业 KB 产品，可信度 > 可用性 > 可扩展性。因此我们的排序是：

| 优先级 | 项 | 一句话定位 |
|---|---|---|
| **P2.5.1** | Citation Validator + 证据链约束 | 决定能不能**卖** |
| **P2.5.2** | 真正的多轮上下文 | 决定日常**用起来顺不顺** |
| **P2.5.3** | Agent Definition Manifest | 为 P2.3 命名 subagent / 未来垂直模板**铺路** |

### 不做 / 延到 Phase 3

外部评审还提到任务生命周期、replay、工具 policy hook 等，我们判断：

- **任务生命周期从 HTTP 解耦**（`agent_task` 表 + worker queue + cancel/resume）：当前保障房场景 p50 约 35s、p95 < 2min，HTTP/SSE 完全扛得住。建 worker queue 是过度投资。**触发条件**：出现 > 5min 的任务或客户明确要求后台运行时再做
- **完整 transcript replay**：P2.3 `result_preview` 的 4KB 快照够当前诊断用。真要做得接对象存储，成本回报不对。**触发条件**：出现第一次事故复盘或计费纠纷
- **工具 policy hook / permission mode / fork-agent**：claude-code-ref 这套是"代码 Agent"场景，KB 场景权限已经由 P2.1 RBAC + Agent Definition 的 `allowed_tools` 覆盖 95%。不抄
- **Prompt cache / fork cache 优化**：我们用 DeepSeek / Qwen，Anthropic prompt cache 不适用；SDK session resume 也不保证跨 provider。**只能走"手动拼 history + compact summary"这一条路**（P2.5.2 就是做这个）

---

## 三、参考的 claude-code-ref 模块（精确映射）

参考树根在 `~/Opensource/vendor/claude-code-ref/`。下面列每件事要读哪个文件、映射到我们哪里、**要照抄什么 / 不抄什么**。

| 我们要做的 | claude-code-ref 对应文件 | 抄什么 | 不抄什么 |
|---|---|---|---|
| **Agent Definition manifest** | `packages/builtin-tools/src/tools/AgentTool/loadAgentsDir.ts` | Zod schema 结构：`description` / `tools` / `disallowedTools` / `prompt` / `model` / `maxTurns` / `permissionMode` / `mcpServers` | plugin loading、markdown frontmatter、agent color、GrowthBook gating |
| **built-in 示例** | `packages/builtin-tools/src/tools/AgentTool/built-in/{exploreAgent,planAgent,generalPurposeAgent,verificationAgent}.ts` | 一 agent 一文件、`getSystemPrompt()` + `whenToUse` + `tools` 白名单 的声明式风格 | 代码场景 prompt 措辞（要重写成 KB 场景） |
| **subagent runtime** | `packages/builtin-tools/src/tools/AgentTool/runAgent.ts` | `createSubagentContext` 思路、`recordSidechainTranscript`、`createSubagentTrace`（Langfuse 子 span）| `killShellTasksForAgent`、`fileStateCache`、worktree 隔离、MCP 动态连接 |
| **resume 思路（多轮上下文）** | `packages/builtin-tools/src/tools/AgentTool/resumeAgent.ts` | resume 的**语义**：把完整历史 messages 塞回 query | 具体实现（Anthropic SDK 的 session ID，不跨 provider） |
| **fork 思路（cache-safe 历史注入）** | `packages/builtin-tools/src/tools/AgentTool/forkSubagent.ts` | `FORK_BOILERPLATE_TAG` + `createUserMessage` 把 "父上下文" 作为一条合成 user message 注入的做法 | 实验门控 + 递归 fork guard（我们 depth=1 已经卡了） |
| **sidechain transcript 记录** | `src/utils/sessionStorage.ts`（`recordSidechainTranscript` / `setAgentTranscriptSubdir` / `writeAgentMetadata`） | 子 agent 对话独立目录的**分层索引**思路 | 文件系统实现（我们用 DB 表 `agent_v2_subagent_message`，已在 P2.3 做了骨架） |
| **subagent 生命周期 hook** | `src/utils/hooks.ts::executeSubagentStartHooks` + `src/utils/hooks/sessionHooks.ts` | hook 入口在"派子之前"、"子结束之后"的**位置** | 完整 hook manifest loading |
| **coordinator / worker 分工** | `src/coordinator/{coordinatorMode.ts,workerAgent.ts}` | 什么时候 coordinator 能 delegate、什么时候自己干的**判断逻辑** | coordinator mode 本身（我们不做，P2.3 的 `spawn_subagent` 足够） |

**注意**：所有引用都是"**学模式不抄代码**"。claude-code-ref 是 Bun + TS、场景是代码 Agent，我们是 Python + Claude Agent SDK、场景是 KB Agent。不会 import 它的任何文件。

---

## 四、P2.5.1 — Citation Validator + 证据链约束

**目标**：Agent 回复里出现的所有 `[N]` 脚注和数值型断言，都必须能映射到本轮实际检索到的 chunk。不能映射的直接拒绝输出或降级为"未查到"。

### 4.1 威胁模型

观察到的几种"看似有引用、实则幻觉"：

1. **脚注错标**：引用的 `[3]` 实际来自另一份文件的段落，但 LLM 标到了相似主题的 chunk 上
2. **数字幻觉**：chunk 里写"申请人须年满 18 周岁"，LLM 答成"年满 21 周岁"并标 `[1]`
3. **合成编造**：LLM 跨 chunk 合成"本科 45 岁以下 + 专科 35 岁以下"这种不存在的条款（M1.5 原版 Dialog 就踩过）
4. **引用漂移**：chunk 讲"公租房"，LLM 拿它支撑"人才房"的论述

### 4.2 架构

```
LLM 最终 assistant text
      │
      ▼
┌─────────────────────────┐
│ CitationExtractor       │  从 Markdown 里抽所有 [N] + 数字 + 金额 + 百分比 + 年限
└─────────┬───────────────┘
          ▼
┌─────────────────────────┐
│ EvidenceIndex           │  本轮所有 rag_retrieve / rag_read_doc 返回的 chunks
│                         │    → normalize → 建立倒排（数字 → chunk_id）
└─────────┬───────────────┘
          ▼
┌─────────────────────────┐
│ CitationValidator       │  逐项检查：
│                         │    a) [N] 指向的 chunk_id 存在
│                         │    b) 数字型断言必须在某 chunk 的原文里出现
│                         │    c) 主题漂移用 embedding 相似度兜底（可选 v2）
└─────────┬───────────────┘
          ▼
┌─────────────────────────┐
│ Outcome                 │
│   - all_valid → pass    │
│   - invalid  → rewrite  │  让 LLM 重写（硬执行模式）或标红警示（软执行）
└─────────────────────────┘
```

### 4.3 实现位置

- 新文件 `api/agent_v2/validators/citation.py`
- 新文件 `api/agent_v2/validators/evidence_index.py`
- 在 `runner.py::run()` 的 **Result 事件发出前** 插一层 validator 钩子
- 验证失败时的动作由会话级开关决定（见 4.5）

### 4.4 数据流

```python
# api/agent_v2/validators/evidence_index.py
@dataclass
class Evidence:
    chunk_id: str
    doc_id: str
    doc_name: str
    text: str
    # 预抽取的结构化内容，validator 不再重新分词
    numbers: list[NumberMatch]       # {value, unit, span, context_window}
    dates: list[DateMatch]
    percentages: list[PercentMatch]

class EvidenceIndex:
    """每个 Agent turn 独立一个实例，收集本轮所有 tool_result 里的 chunks。"""
    def add_from_rag_retrieve(self, result: dict) -> None: ...
    def add_from_rag_read_doc(self, result: dict) -> None: ...
    def lookup_by_citation_id(self, n: int) -> Evidence | None: ...
    def find_numerical_support(self, number: NumberMatch) -> list[Evidence]: ...
```

### 4.5 会话级开关（写进 `agent_v2_session` 表新列）

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `citation_enforce_level` | VARCHAR(16) | `warn` | `off` / `warn` / `strict` |
| `citation_numeric_strict` | BOOL | 0 | 数字/金额/年限/日期必须有原文支撑 |

三档语义：
- **off**：不校验，性能最快，仅 demo 用
- **warn**（默认）：发现问题不拒绝，只在 SSE 流追加 `citation_warning` 事件，前端渲染为脚注旁红色警示
- **strict**：发现问题直接 block 最终答复，追一轮 LLM 重写（system prompt 里标明"上一版引用校验失败"）；最多重写 1 次，否则降级为"未查到可靠来源"

### 4.6 事件扩展（`api/agent_v2/event.py`）

```python
class CitationIssue(TypedDict):
    kind: Literal["missing_chunk", "number_unsupported", "topic_drift"]
    citation_index: int | None        # [N] 里的 N
    claim: str                         # 有问题的那句话或数字
    detail: str

class CitationWarningEvent(Event):
    type: Literal["citation_warning"] = "citation_warning"
    issues: list[CitationIssue]
    level: Literal["warn", "strict_rewritten"]
```

### 4.7 对标 claude-code-ref

claude-code-ref 没有 citation validator（不是 KB 场景），但有**结构类似**的 post-response hook：`executeSubagentStartHooks`（`src/utils/hooks.ts`）允许在 subagent 开始 / 结束时插入任意校验。我们抄"在哪个生命周期点插"，**不抄**"怎么加载 hook manifest"—— validator 直接硬编码进 runner，**不**做成可插拔 hook 系统（那是 Phase 3）。

### 4.8 验收

- 保障房 10 题 golden set 重跑：每题人工打分同时自动跑 validator，`warn` 模式下**每一次**人工扣分应至少对应一条 validator 警告（否则是 validator 漏报）
- 构造 5 道"LLM 易编数字"反面用例：保障房社保年限、首付比例、面积上限、年龄条件、家庭人数。在 `strict` 模式下应该都降级为"未查到"
- 性能：单次验证 < 50ms（100 chunks × 10 断言）

---

## 五、P2.5.2 — 真正的多轮上下文

### 5.1 最小可用方案

**核心改动**：`agent_v2_app.py::conversation()` 里构造 `AgentRunner` 之前，拉历史消息组装成 SDK 能吃的格式，传给 runner。

```python
# BEFORE
async for ev in runner.run(user_message):
    ...

# AFTER
history = await AgentV2MessageService.list_for_runner(
    session_id=session.id,
    limit=session.history_turn_limit,  # 新列，默认 10 轮
    exclude_message_id=assistant_msg_id,  # 本轮刚落库的空 assistant msg 不算
)
async for ev in runner.run(
    user_message=user_message,
    history=history,  # 新参数
):
    ...
```

### 5.2 `AgentRunner.run()` 签名扩展

```python
async def run(
    self,
    user_message: str,
    history: list[HistoryMessage] | None = None,  # 新
) -> AsyncIterator[Event]:
    ...
    async for msg in query(
        prompt=self._build_prompt_with_history(user_message, history),
        options=options,
    ):
        ...
```

### 5.3 `_build_prompt_with_history` 实现（两条路径）

**路径 A — 原生多消息（首选）**：Claude Agent SDK `query()` 支持传 `messages` 而不是单一 `prompt`。如果 provider 支持多消息，直接走这条。

**路径 B — 合成 user message（兜底）**：对 SDK/provider 不支持多消息的情况（部分 DeepSeek 路径），参考 claude-code-ref 的 `forkSubagent.ts::FORK_BOILERPLATE_TAG` 模式，把历史压成一条合成 user message：

```
<conversation-history>
[previous] user: ...
[previous] assistant: ...
[previous] tool_use: rag_retrieve(...) → {...}
</conversation-history>

<current>
{user_message}
</current>
```

选哪条路径在 `runner.py` 里按 `model_config.provider` 决定。

### 5.4 Compact Summary（超过窗口时）

当 history 累积超过阈值（默认 20 轮 或 Token 预算的 30%）时，触发 compact：

1. 后台调一次便宜模型（`deepseek-chat` 小上下文）总结前 N - 5 轮
2. 总结 + 最近 5 轮作为新 history
3. 把总结写回 `agent_v2_session.summary_text` + `summary_until_seq`

参考 claude-code-ref `SystemCompactBoundaryMessage` 类型（`runAgent.ts` 第 30 行附近 import），他们也有类似机制（5-min cache TTL 触发的 compact）。**我们不抄 cache TTL 逻辑**（不用 Anthropic cache），只抄"compact boundary 是 session 里的一个标记消息"这个模型。

### 5.5 新 DB 列（不建新表，直接加在 `agent_v2_session`）

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `history_turn_limit` | INT | 10 | 每次调 Agent 回看多少轮 |
| `summary_text` | TEXT NULL | NULL | compact 后的摘要 |
| `summary_until_seq` | INT NULL | NULL | 摘要覆盖到第几条消息 |

全部 nullable，不破坏已有 session。

### 5.6 验收

- 保障房场景追问用例：
  1. Q：公租房申请条件？ → A：...
  2. Q：**刚才那里面** 年龄限制呢？ → 必须在本轮 rag_retrieve 前就识别出 "那里面" 指公租房（靠历史上下文），而不是重新全库检索
- 20 轮以上长对话：compact 触发后，token 用量应回落到约 ≤ compact 前 60%
- 两个 provider（DeepSeek、Claude）各跑一次验证路径 A/B 都能工作

### 5.7 对标 claude-code-ref

- **history 塞回**：参考 `resumeAgent.ts`（session 恢复）+ `forkSubagent.ts`（`createSubagentContext` 把父 messages 传给子）
- **compact**：参考 `runAgent.ts` 里 `SystemCompactBoundaryMessage` 类型
- **不抄**：Anthropic 独有的 session ID 复用 / cache breakpoint 检测（`cleanupAgentTracking` in `src/services/api/promptCacheBreakDetection.js`）

---

## 六、P2.5.3 — Agent Definition Manifest

### 6.1 目标

把 Agent 和 subagent 的定义从"硬编码 dict + 模板文件 + DB session 列"统一成**声明式 manifest**。一个 manifest 描述一个 Agent 的全部行为契约。

### 6.2 Manifest Schema

```python
# api/agent_v2/definitions/schema.py
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class AgentDefinition:
    # 身份
    name: str                              # "baozhang_advisor", "fact_checker"
    version: str                           # "1.0.0"
    description: str                       # 人类可读
    when_to_use: str                       # 父 Agent 决定要不要派时看这句（同 claude-code-ref `whenToUse`）

    # 行为
    system_prompt: str | SystemPromptFn   # 可以是字符串，也可以是运行时生成函数（带 kb_ids 等）
    model: ModelRef | Literal["inherit"]   # 具体模型 或 "跟父一样"
    max_turns: int = 20
    max_budget_usd: float = 0.5

    # 工具
    tools: list[str] | Literal["*"]        # 白名单；"*" = 父的全集；claude-code-ref 同语义
    disallowed_tools: list[str] = ()       # 黑名单优先级高于白名单

    # 数据范围
    kb_scope: Literal["inherit", "explicit"] = "inherit"
    explicit_kb_ids: list[str] = ()        # 仅 kb_scope="explicit" 生效

    # Validator
    citation_enforce: Literal["off", "warn", "strict"] = "warn"
    citation_numeric_strict: bool = True

    # subagent 能力
    can_spawn_subagents: bool = False      # 只有少数 supervisor 能派
    allowed_subagent_types: list[str] = () # 能派哪些命名 subagent
```

### 6.3 内置 Agent 目录

```
api/agent_v2/definitions/
├── schema.py
├── registry.py                   # load_all() 扫 built_in/ + DB 自定义定义
├── built_in/
│   ├── supervisor_general.py    # 通用 Agent（当前的默认）
│   ├── supervisor_baozhang.py   # 保障房顾问（从现有模板迁过来）
│   ├── sub_policy_researcher.py  # subagent: 聚焦研读单一政策
│   ├── sub_evidence_checker.py   # subagent: 给出答案后专查证据链
│   ├── sub_summary_writer.py     # subagent: 多份 chunks → 对比表
│   └── sub_citation_auditor.py   # subagent: 和 P2.5.1 validator 协作的重写手
```

每个文件结构对标 claude-code-ref `built-in/exploreAgent.ts` / `planAgent.ts`：一文件一 Agent、一个 `getSystemPrompt()` 函数 + 一个 definition 对象 export。

### 6.4 `spawn_subagent` 升级：支持 `subagent_type`

```python
# 当前
spawn_subagent(description, prompt, allowed_tools?, max_turns?)

# 升级后
spawn_subagent(
    description,
    prompt,
    subagent_type?,      # 新：命名 subagent；空 = 通用（行为等同现在）
    allowed_tools?,      # 覆盖 definition.tools
    max_turns?,
)
```

路由：
```python
if args.get("subagent_type"):
    definition = AgentRegistry.get(args["subagent_type"])
    if not definition:
        return {"error": f"unknown subagent_type: {args['subagent_type']}"}
    if definition.name not in ctx.allowed_subagent_types:
        return {"error": f"current agent cannot spawn {definition.name}"}
    # 用 definition 的 tools / model / prompt 组装子 runner
else:
    # 保持现有"通用 spawn"逻辑
```

### 6.5 DB：自定义 Agent 仍走 DB

内置 Agent 从 `definitions/built_in/` 加载。企业用户自定义 Agent 存 `agent_v2_definition` 表（schema 和 `AgentDefinition` 一一对应，加 `tenant_id` / `create_time` / `created_by`）。Registry 合并两个来源，内置优先。

### 6.6 迁移策略

- 现有 `agent_v2_session.system_prompt` / `max_turns` / `max_budget_usd` 列**保留**，作为"session 级 override"
- Session 创建时可选传 `agent_definition_name`；传了就用 definition 的默认值，session 列留空
- 老 session（没有 definition_name）继续按旧路径走，完全向后兼容

### 6.7 前端

`NewSessionDialog` 顶部的"模板卡片"从硬编码 6 个（M1.6 做的）改成从 `/v1/agent_v2/definition` 端点拉。模板编辑 UI 放 Phase 3。

### 6.8 对标 claude-code-ref

| 点 | 对应 |
|---|---|
| Manifest schema | `loadAgentsDir.ts` 的 Zod schema |
| 一文件一 Agent | `built-in/{exploreAgent,planAgent,...}.ts` |
| `when_to_use` 字段 | 同名字段，用于 LLM 决定要不要派 |
| `tools: "*"` 语义 | 对齐 `forkSubagent.ts` 的 `tools: ['*']` + `useExactTools` |
| `model: "inherit"` | 同名语义 |
| `allowed_subagent_types` | 对齐 `AgentTool` 只在 coordinator / 主 Agent 暴露的思路 |

**不抄**：`permissionMode`（KB 场景权限已由 RBAC 管）、`mcpServers`（我们当前无 MCP 依赖）、color / display 元数据、plugin 加载、GrowthBook / feature flag、hook frontmatter。

### 6.9 验收

- 把现有 6 个模板（M1.6）平移成 6 个 definition，行为完全等价
- 新加一个 `sub_citation_auditor` definition，和 P2.5.1 validator 的 `strict` rewrite 路径联动（validator 失败时 supervisor 可以选择派这个子来重写而不是自己重写）
- 单测：`spawn_subagent(subagent_type="sub_policy_researcher")` 能正确加载 definition、拒绝 `allowed_subagent_types` 之外的类型、拒绝 definition 不存在的情况

---

## 七、实施顺序与时间估算

| 阶段 | 交付 | 预计耗时 |
|---|---|---|
| **P2.5.1-a** | EvidenceIndex + CitationExtractor + `warn` 模式 | 2 天 |
| **P2.5.1-b** | `strict` 模式 + rewrite 循环 + 前端警示徽标 | 1 天 |
| **P2.5.1-c** | 5 道反面用例 + 保障房 10 题 validator 重跑 | 0.5 天 |
| **P2.5.2-a** | `AgentRunner.run(history=...)` 签名扩展 + 路径 A/B | 1 天 |
| **P2.5.2-b** | Compact summary + session 三个新列 + 自动迁移 | 1 天 |
| **P2.5.2-c** | 追问场景验收 | 0.5 天 |
| **P2.5.3-a** | `AgentDefinition` schema + registry + built_in 目录 + 平移 6 模板 | 1.5 天 |
| **P2.5.3-b** | `spawn_subagent` 支持 `subagent_type` + 新 subagent 定义 | 1 天 |
| **P2.5.3-c** | 前端 NewSessionDialog 走新端点 + 验收 | 0.5 天 |

合计约 **9 天**。实际按一个里程碑一提交的节奏。

---

## 八、风险与回退

| 风险 | 概率 | 缓解 |
|---|---|---|
| Validator 漏报（真幻觉没抓到） | 中 | 人工评测持续跑 golden set；发现漏报就扩 EvidenceIndex 的规则 |
| Validator 误报（正确答案被拒） | 中 | `warn` 为默认；`strict` 只在明确安全场景开 |
| 多轮 history 拼接导致 token 爆 | 中 | `history_turn_limit` 默认 10；compact 触发阈值保守（20 轮） |
| DeepSeek 多消息路径不稳 | 中 | 路径 A/B 两条路，路径 B 已验证（合成 user message） |
| Agent Definition 迁移破坏老 session | 低 | 新列 nullable；老 session 不读 definition，完全按旧路径走 |
| claude-code-ref 模式在 Python 异构 SDK 上水土不服 | 中 | **只抄模式不抄代码**，每个点都有"不抄什么"清单 |

---

## 九、和已有 PLAN 的关系

- `PLAN.md`：Phase 2.5 作为 Phase 2 → Phase 3 之间的成熟化窗口，不是新增 Phase
- `PLAN-phase2.md`：本 Phase 是 Phase 2 的**补课**，三件事都没影响 P2.1/2.2/2.3 已完成的功能
- `PLAN-multi-agent.md`：P2.5.3 会回写 `spawn_subagent` 签名（加 `subagent_type`），兼容旧调用
- `PLAN-rbac.md` / `PLAN-bot-channels.md`：不受影响
- `DESIGN.md`：Phase 1 架构稳定，不改

---

## 十、Non-goals（明确不做）

- ❌ 通用 agent runtime（worktree、fork agent、permission mode bubble、MCP hot-reload）
- ❌ Task lifecycle decouple（agent_task 表 + worker queue + cancel/resume）— 等有真长任务再做
- ❌ 完整 transcript + replay / Langfuse 深度集成 — 等事故复盘需要时再做
- ❌ Tool policy hook / manifest — Agent Definition 的 `tools` / `disallowed_tools` 已覆盖需求
- ❌ Prompt cache 优化 — 非 Anthropic provider 不适用
- ❌ Agent 可视化编排器 — Phase 3 产品化再说

---

## 十一、版本记录

| 日期 | 版本 | 决策 |
|---|---|---|
| 2026-04-23 | v0.1 | 初稿。优先级：可信度 > 可用性 > 可扩展性；任务生命周期延至 Phase 3；全程对标 `vendor/claude-code-ref` 但只抄模式不抄代码 |
