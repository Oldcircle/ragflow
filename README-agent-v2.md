# RAGFlow Agent v2 — 使用指南

> Agent-first 企业知识库助手，基于 Claude Agent SDK + RAGFlow 的 RAG 引擎。
> 原 RAGFlow Dialog 功能并存不替换，本页只覆盖 Agent v2。
>
> **截至 Phase 2.6 v0.6 完成（2026-04-23）**：基础 RAG Agent + 数据集 RBAC +
> IM 机器人渠道 + Multi-Agent + 引用校验 + 多轮上下文 + Agent Definition
> manifest + **文档运营工具**（18 MCP 工具）+ **自维护 KB 能力**（sub_librarian
> 体检 + 写笔记）+ **真 runtime plan gate**（破坏性写操作硬审批）+ **plan 执行
> 闭环**（get_pending_plan + `[step K/N done]` 进度标记）。

## 一、它是什么

**一句话**：把 RAG 作为 Agent 可调用的工具，而不是"每轮强制拼进 prompt"；
Agent 不仅能答问题，还能按用户要求**维护 KB 的形态**（打标签、重命名、归档、
重解析、从 URL 入库、新建 KB、写调研笔记）。
- LLM 自主决定何时检索（事实相关问题必查、闲聊不查）
- 多轮迭代（查不到换关键词再查）
- 输出**自动核对证据链**：数字、年限、金额、引用编号必须有原文支撑，否则提示用户
- **真正的多轮记忆**：追问场景能识别「刚才」「上面那条」的指代关系
- 支持 Multi-Agent：主 Agent 可以派**命名 subagent**（archivist / librarian /
  policy_researcher / evidence_checker）做聚焦任务
- **破坏性操作受硬审批管辖**（Phase 2.6 v0.4）：批量 / 跨 KB 等都必须先
  `submit_plan`，用户消息前缀 `[plan approved]` 才能执行
- 支持 DeepSeek / Claude 等模型

和 RAGFlow 原版 Dialog 的区别：

| 维度 | 原 Dialog | Agent v2 |
|---|---|---|
| 检索策略 | 每轮强制一次 | 模型自主，可多轮换关键词 |
| 防幻觉 | 依赖 prompt + 引用插入 | System Prompt 严格约束 + **后置 Citation Validator 校验数字 / [N] 脚注** |
| 工具链 | 单 RAG 工具 | **18 个工具**：4 读 + spawn_subagent + 6 写 + 4 自省/总结 + 3 交互 / plan（详见 §四） |
| 多轮上下文 | 只发送当前 user message | **完整 history + compact summary**，追问场景不失忆 |
| Multi-Agent | ❌ | ✅ 主 Agent 可派独立 context 的子 Agent（含命名 `subagent_type`） |
| 访问控制 | 只有 me / team 两档 | **完整 RBAC**（VIEWER / CONTRIBUTOR / ADMIN / OWNER 四角色 + 审计日志 + v0.4 runtime plan gate） |
| IM 渠道 | iframe embed | **飞书机器人**（webhook + 签名 + 会话映射） |
| 可观测性 | 日志为主 | 前端右侧实时展示每次工具调用的 args + result，子 Agent trace 可下钻 |
| 扩展 | 不易 | Python `@tool` 注册即可；Agent/subagent 走声明式 `AgentDefinition` manifest；工具注解集中在 `api/agent_v2/annotations.py` |

## 二、快速开始

### 前置条件

1. RAGFlow 已跑起来（见项目 `CLAUDE.md`）
2. 在 RAGFlow **模型供应商** 页面添加至少一个 Chat 模型：
   - DeepSeek（推荐，便宜）— 会自动走 `/anthropic` 兼容端点
   - Anthropic — 直接用 Claude 官方
   - OpenAI-API-Compatible / VLLM / Ollama（需对方支持 Anthropic 协议）

   > Agent v2 不再依赖 `AGENT_V2_DEEPSEEK_KEY` 等环境变量。
   > 旧 env var 作为兜底保留，但你可以在「模型供应商」页改 key，Agent 立刻生效。
3. 知识库已至少有一个解析完成的 KB（Embedding 建议 `bge-m3`）

### 打开工作台

浏览器访问 **<http://localhost:9222/agent-chat>** 或顶部导航栏点「工作台」。

### 新建一个 Agent 会话

1. 左侧 **新建会话**
2. **可选：顶部选一个模板**（保障房 / 政策 / 法务 / 研报 / 客服 / Wiki）
   - 模板会自动填好 System Prompt 和默认参数（包含 [N] 引用规范）
3. 填写剩下的：
   - 名称（可改模板自带的）
   - 知识库：勾选 ≥ 1 个（多选需共享同一 embedding 模型）
   - 模型：从下拉选你配过的 Chat 模型
4. **创建**

### 对话

- 中间区域发消息，支持 Markdown 渲染（加粗、表格、代码块、数学公式）
- Agent 答复里带有 **`[1] [2]` 上标** 的地方表示引用对应的 chunk；点击上标自动滚动到下方引用卡片
- 答复下方会出现 **「引用来源」** 卡片，列出用到的政策/文档 + 可展开的 chunk 列表（含相似度 + 页码）
- 右侧 **工具调用** 面板实时展示 Agent 调用的每个工具（args / result / 耗时 / 状态）
- 派了 **subagent** 时，工具卡片内会展开子 Agent 任务描述 + 执行状态 + 最终结果预览
- 答复里如果有数字/脚注**无原文支撑**，会在答复下方弹出 **「引用校验提示」** 面板，逐条列出可疑断言
- 点右上角 **停止** 可中断流式响应

## 三、推荐 System Prompt 模板

默认模板已包含严格防幻觉指令，新建会话时不用改。若想自定义：

```
你是 <领域> 顾问，严格基于知识库内容答复。

工作方式：
1. 判断问题是否涉及本领域；无关则礼貌拒答。
2. 相关问题：必须调用 rag_retrieve 工具检索原文，可多次换关键词。
3. 严格基于检索结果答复：
   - 所有数字、年限、比例、金额必须原文支撑（后置 Citation Validator 会自动核对）
   - 原文没写的内容，回答"未查到相关规定，建议咨询 <业务方>"
   - 不要把其他场景的规则套到本知识库
4. 引用规范：每个有依据的句子末尾加 [1] [2] 上标，对应本轮检索顺序

禁止：
- 编造数字、名称、时间
- 用训练知识补充原文未说的内容
```

## 四、工具清单（18 个，Phase 2.6 v0.6）

按分层列出。Supervisor 默认只拿 8 个（全部读 + 轻量体检 + 委派 + 用户交互），
写/审计/总结/plan-execution 工具必须**通过 `spawn_subagent` 派到对应 subagent**
才能用——这条架构分离在 v0.3 把 supervisor `tools=SUPERVISOR_TOOLS` 写死后
不可绕过。

### Supervisor 直接可调（8）

| 工具 | 用途 |
|---|---|
| `rag_retrieve` | 语义检索（向量+BM25 融合）。KB 问答默认入口 |
| `rag_list_docs` | 列 KB 文档元数据，可 keywords 过滤 |
| `rag_read_doc` | 按 doc_id 读整份文档（分页） |
| `rag_graph_query` | GraphRAG 实体/关系查询（KB 须启用 KG） |
| `kb_stats` | 轻量体检：文档数 / chunk 数 / 最近改动时间。<1KB 响应，<100ms |
| `spawn_subagent` | 派一个独立 context 的子 Agent，可选 `subagent_type` 命名路由 |
| `ask_user_question` | 2-4 选项结构化澄清；SSE 卡片交互 |
| `submit_plan` | 提交执行计划等用户审批；Phase 2.6 v0.4 起触发真运行时 gate |

### 通过 `spawn_subagent` 才能用（9）

都在 `api/agent_v2/tools/doc_ops/`，共享 `@require_kb_write` 装饰器
（RBAC + 审计 + idempotency + v0.4 plan gate）。

**写 / 命令式运营**（sub_archivist 专用，6 个）：

| 工具 | 用途 | 是否受 plan gate |
|---|---|---|
| `doc_tag` | 加 / 删 / 设文档标签 | ✅ |
| `doc_rename` | 改文档显示名（自动保扩展名） | ✅ |
| `doc_archive` | 跨 KB 移动（自动校 embedding 一致） | ✅ |
| `doc_reparse` | 清 chunk 重建索引 | ✅ |
| `doc_upload_from_url` | 从 URL 拉内容入库（SSRF / Content-Length 硬防护） | ✅ |
| `kb_create` | 新建 KB（授予 caller OWNER） | ✅ |

**自省 / 总结 / 写笔记**（sub_librarian 专用，3 个 + 1 个跳 gate 的写）：

| 工具 | 用途 | 是否受 plan gate |
|---|---|---|
| `kb_audit` | 跑一次 KB 体检：陈旧文档、重复、空洞覆盖面 | — (只读) |
| `doc_list_recent_changes` | 最近 N 小时的 audit log 流水 | — (只读) |
| `doc_create_note` | 把调研结果写成 markdown 笔记存回 KB（自动 tag、dedup） | **❌ 显式 plan_gated=False**，低风险可删 |

**plan 执行闭环**（sub_archivist 专用，1 个；Phase 2.6 v0.6 新增）：

| 工具 | 用途 | 是否受 plan gate |
|---|---|---|
| `get_pending_plan` | 批准之后读回完整 plan payload（title / steps / affected_resources / risk），archivist 据此逐步执行并 emit `[step K/N done: ...]` 进度标记 | — (只读) |

### 两个 supervisor / 两个 subagent 的架构分离（Phase 2.6 v0.2 起）

- **supervisor**（6 种内置定义：保障房 / 通用政策 / 合同法务 / 研究 / 客服 / wiki）
  只做检索 QA + 委派；不能直接写
- **sub_archivist**（v1.2.0）专注**动手改**；system prompt 明确 runtime
  plan gate 规则
- **sub_librarian** 专注**看 + 想 + 写笔记**；发现 KB 问题会自己调 `kb_audit`
  → 用 `doc_create_note` 写成笔记入库
- **sub_policy_researcher / sub_evidence_checker**（Phase 2.5.3）保留做深度
  条款研读 / 引用核对

```
spawn_subagent(
    description="给合同-2026 做季度体检",
    prompt="跑 kb_audit 找陈旧合同；把发现写成 markdown 笔记入库",
    subagent_type="sub_librarian"  # 必填，否则派出的是通用子、拿不到体检工具
)
```

### submit_plan 审批流程（v0.4 起硬约束，v0.6 闭环）

对批量 ≥3、跨 KB 移动、外部 URL 入库、新建 KB 等场景，archivist 会先调
`submit_plan` emit 计划卡片；前端用户点 Approve / Reject / Request Changes；
下一轮用户消息以对应前缀（`[plan approved]` / `[plan rejected]` /
`[plan request changes]`，中文同样生效：`[计划批准]` / `[计划拒绝]` /
`[计划修改]`）回来即可。端点自动剥前缀、落 DB、调度 Agent 继续。

**没批之前**，任何受 gate 管辖的写调用都会返 `error: plan_gate` + 写 deny 审计。
1h 没回复自动 TTL 过期。

**批准之后**（v0.6）：supervisor 再次派 archivist，后者第一件事是调
`get_pending_plan` 读回完整 steps，然后逐步执行。每步执行完 emit 一行
`[step K/N done: <verb> <resource>]` 让用户追踪；失败则 `[step K/N FAILED:
<reason>]` + 停止批量。最后一行汇总 `N succeeded, M failed`。
`pending_plan_body` 在 turn 收尾自动清，下一轮重新需要 plan 则再走一次
`submit_plan`。

**新增工具**：在 `api/agent_v2/tools/` 放 `@tool` 装饰的 async 函数 → 注册到
`registry.py` → 在 `api/agent_v2/annotations.py::ANNOTATIONS` 补一条
`ToolAnnotation`（`test_annotations.py` 有 parity 断言，忘了会挂）→ 前端自动可见。

**新增 subagent 定义**：在 `api/agent_v2/definitions/built_in/` 加 `.py`
文件 export `DEFINITION = AgentDefinition(...)`；`tools` 字段列工具短名；
`citation_enforce` 决定是否强制 [N] 脚注。

## 五、后端端点

### Agent v2 核心

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/v1/agent_v2/session` | 创建会话（可选字段：`citation_enforce_level`、`citation_numeric_strict`、`history_turn_limit`） |
| GET | `/v1/agent_v2/session` | 列我的会话 |
| GET | `/v1/agent_v2/session/<id>` | 会话详情 + 消息 + 工具调用 |
| DELETE | `/v1/agent_v2/session/<id>` | 软删 |
| GET | `/v1/agent_v2/session/<id>/subagent` | 列该会话派过的子 Agent trace |
| GET | `/v1/agent_v2/tool` | 列所有工具 |
| GET | `/v1/agent_v2/model` | 列用户 TenantLLM 中的 Chat 模型 |
| GET | `/v1/agent_v2/template` | 列 6 个预置 Agent 模板（M1.6，硬编码；逐步迁到 /definition） |
| GET | `/v1/agent_v2/definition?kind=supervisor\|subagent` | **Phase 2.5.3 / 2.6 v0.2** — 列 `AgentDefinition` 清单（10 个：6 supervisor + 4 subagent） |
| POST | `/v1/agent_v2/conversation` | 发消息（SSE 流式） |

### 周边能力（Phase 2 + 3.1 + 3.2）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET/POST/DELETE | `/v1/kb/<kb_id>/member` | 数据集成员管理（P2.1 RBAC） |
| GET | `/v1/audit_log/list` | 访问审计流水（P2.1） |
| POST | `/v1/bot/<channel>/<account>/events` | IM 机器人 webhook（P2.2，飞书） |
| GET/POST/PUT/DELETE | `/v1/bot_channel/*` | IM 渠道 CRUD |
| GET | `/v1/tenant_quota` | 租户配额 + 今日/本月用量（P3.1b） |
| GET/POST/PUT/DELETE | `/v1/agent_trigger/*` | 定时触发器（P3.2，Cron） |

所有端点要登录 cookie（除 `POST /v1/bot/<channel>/<account>/events` 是公网 webhook 用签名校验）。

### SSE 事件类型

后端通过 `POST /v1/agent_v2/conversation` 流式返回，事件全清单：

| 事件 | 数据 | 说明 |
|---|---|---|
| `text_delta` | `{text}` | 答复文本增量 |
| `thinking` | `{text}` | extended thinking 增量（部分模型） |
| `tool_call_start` | `{id, name, args}` | 工具调用开始 |
| `tool_call_end` | `{id, result, error, duration_ms}` | 工具调用结束 |
| `subagent_start` | `{trace_id, description, parent_tool_call_id, allowed_tools, max_turns, max_budget_usd}` | 子 Agent 派发开始（P2.3） |
| `subagent_end` | `{trace_id, status, result_preview, cost_usd, duration_ms, token_usage}` | 子 Agent 结束 |
| `citation_warning` | `{issues, level}` | **Phase 2.5.1** — 答复里 [N] / 数字断言未通过校验 |
| `ask_user_question` | `{pending_id, header, question, options, multi_select}` | **Phase 2.6** — Agent 想要用户结构化选一个 / 几个；前端渲染为选项卡片 |
| `plan_submitted` | `{pending_id, title, steps, affected_resources, risk_level, estimated_cost_usd, reversible, reversible_hint}` | **Phase 2.6** — Agent 提交执行计划等审批；前端渲染审批卡片，用户下一轮用 `[plan approved\|rejected\|request changes]` 前缀回复 |
| `error` | `{code, message}` | 流式错误（非致命） |
| `end` | `{usage}` | 流结束，带 token/cost/duration 用量 |

完整类型定义见 `api/agent_v2/event.py`。

## 六、程序化使用（SDK / CLI）

### Python CLI

```bash
export AGENT_V2_DEEPSEEK_KEY=sk-xxx
export NLTK_DATA=./nltk_data

.venv/bin/python scripts/test_agent_v2.py \
  --provider deepseek \
  --question "你的问题" \
  --max-turns 8
```

### 在代码里嵌

```python
from api.agent_v2.runner import AgentRunner, ModelConfig

runner = AgentRunner(
    tenant_id="<tenant_uuid>",
    kb_ids=["<kb_uuid>"],
    system_prompt="...",
    model=ModelConfig(
        model="deepseek-chat",
        base_url="https://api.deepseek.com/anthropic",
        auth_token="sk-xxx",
    ),
    max_turns=8,
    max_budget_usd=0.5,
    # Phase 2.5.1 — Citation Validator
    citation_enforce_level="warn",       # off | warn | strict
    citation_numeric_strict=True,
)

# Phase 2.5.2 — 多轮上下文（history / summary 都可选）
history = [
    {"role": "user", "content": "公租房申请条件？"},
    {"role": "assistant", "content": "年满 18 周岁、社保满 3 年 [1]。"},
]
async for ev in runner.run(
    "刚才那条里年龄限制是多少？",
    history=history,
    summary_text=None,          # 长对话时传入 compact summary
):
    print(ev.to_dict())
```

事件类型见 §5 SSE 事件表。

## 七、Phase 2.5 新功能详解

### 7.1 Citation Validator（答案可信度保障）

Agent 答复收尾时自动把文本里的 `[N]` 脚注和所有数字（百分比/年限/金额/年龄/日期）对照本轮检索到的 chunk 做核对。两类问题会触发 `citation_warning` 事件：

- **missing_chunk**：`[N]` 编号越界（比如答复写 [5] 但本轮只检索到 3 条）
- **number_unsupported**：某个数字在任何 evidence chunk 里都找不到字面匹配

**三档执行模式**（在 session 级配置）：
- `off` — 不校验（只用 SDK 原生行为）
- `warn` — 默认。发现问题只发事件提示前端，不拦截答复
- `strict` — 发事件标 `strict_failed`，前端弹红色面板

实现在 `api/agent_v2/validators/{evidence_index,citation}.py`。5/5 smoke 用例覆盖（数字抽取 / 索引往返 / missing_chunk / number_unsupported / 复合幻觉）。

### 7.2 多轮上下文 + Compact Summary

Claude Agent SDK 的 `query()` 只吃单个 prompt 字符串，不像 Chat API 能直接传 messages 列表。我们走**合成 user message** 路径（参考 claude-code-ref `forkSubagent.ts::FORK_BOILERPLATE_TAG`）：把历史拼成 `<conversation-history>` + `<current-user-message>` 两段块注入 prompt。

```
<conversation-history>
[#1] user: 公租房申请条件？
[#2] assistant: 年满 18 周岁、社保满 3 年 [1]。
[#3] user: 那年龄限制是 18 还是 21 岁？
[#4] assistant: 根据 [1]，年龄限制是 18 周岁。
</conversation-history>

<current-user-message>
刚才那里面对收入有什么要求？
</current-user-message>

请基于上面的对话历史理解用户的指代关系...
```

**超过阈值自动 compact**（`api/agent_v2/compactor.py`）：
- 默认累积 20 条消息（≈10 轮）触发 compact
- 保留最近 10 条作为「recent」窗口，其余并入摘要
- 直接 httpx POST `/v1/messages` 做摘要（兼容 Anthropic 和 DeepSeek-anthropic）
- fire-and-forget 异步，不阻塞 SSE 收尾
- 摘要写回 `agent_v2_session.summary_text` + `summary_until_seq`，下一轮自动使用

Session 表新增 3 个 nullable 列（已自动迁移）：
- `history_turn_limit` — 每次回看多少轮（默认 10）
- `summary_text` — 累积摘要
- `summary_until_seq` — 摘要覆盖到第几条消息

### 7.3 Agent Definition Manifest

把 Agent 的行为契约从「硬编码 + DB 列 + 模板文件」统一到声明式 `AgentDefinition`：

```python
# api/agent_v2/definitions/built_in/sub_policy_researcher.py
DEFINITION = AgentDefinition(
    name="sub_policy_researcher",
    version="1.0.0",
    description="深入研读单一政策，回答其细节条款、生效时间、适用范围等",
    when_to_use="父 Agent 已经定位到具体政策文件，需要对某个条款做细粒度追溯",
    kind="subagent",
    system_prompt="你是一名政策研究员 subagent...",
    model="inherit",              # 或 ModelRef(model="...", base_url="...")
    max_turns=6,
    max_budget_usd=0.3,
    tools=["rag_retrieve", "rag_read_doc"],   # 或 "*" 表示继承父
    citation_enforce="warn",
    can_spawn_subagents=False,    # 子不能再派孙
    history_turn_limit=4,
)
```

**路由**：`spawn_subagent({..., subagent_type: "sub_policy_researcher"})` → 查 registry → 检查 `ctx.allowed_subagent_types` 白名单 → 用 definition 的 tools / max_turns / budget / prompt 组装子 runner。

内置 10 个定义：
- **Supervisor（6）**：sz-baojian-house / generic-policy / legal-contract / research-analyst / customer-support / internal-wiki（从 M1.6 的硬编码模板平移；v0.3 全部改成 8 段式英文 prompt）
- **Subagent（4）**：sub_policy_researcher / sub_evidence_checker / sub_archivist（**Phase 2.6 写操作专责**，v1.2.0 含 runtime plan gate 规则）/ sub_librarian（**Phase 2.6 v0.2 新增**：KB 体检 + 写调研笔记）

## 八、性能 & 成本（实测）

从保障房 10 题黄金评测（见 `test/e2e/baojian_house_results_20260422.md`）：

- 单次会话耗时：12–125s（推理题拖后腿）
- 平均每题 35.6s
- 平均每题 3.2 次工具调用
- DeepSeek 成本：平均 $0.11 / 题
- **质量：10 题平均 4.8/5**（防幻觉 3 题全满分）

> Phase 2.5 的 Citation Validator / 多轮 / Definition 三项对 per-turn 延迟影响 < 5%（validator 纯 Python 正则匹配，compact 异步）；compact 触发时额外一次便宜模型调用（$0.002 数量级），20 轮对话才触发一次。

## 九、常见问题

**Q：工具调用面板没更新？**
A：M1.4 之前的 bug，M1.4 修了不可变更新。硬刷新（Cmd+Shift+R）。

**Q：Agent 不调用 `rag_retrieve`？**
A：检查 System Prompt 是否明确"必须检索"。

**Q：Markdown 不渲染？**
A：已在 M1.4 接入 react-markdown。硬刷新。

**Q：DeepSeek 端点连不上？**
A：验证 `AGENT_V2_DEEPSEEK_KEY` 有效；DeepSeek 的 Anthropic 兼容端点在 `https://api.deepseek.com/anthropic`。

**Q：多个 KB 报错"embedding 不一致"？**
A：`rag_retrieve` 限制所有 KB 必须用同一个 embedding 模型。把不同 embedding 的 KB 拆到不同会话。

**Q：`citation_warning` 面板误报？**
A：大多数情况是模型给数字标了 `[N]` 但 chunk 里原文写的是同义数字（如"二十"对"20"）。Phase 2.5.1 的规则只支持阿拉伯数字和小数字中文（一/二/三...十），如果确认是误报，可以在 session 创建时把 `citation_numeric_strict` 关掉。`warn` 模式只发提示不拦截答复。

**Q：追问场景还是识别不出指代？**
A：看 session 的 `history_turn_limit`（默认 10 轮）。如果对话超过 20 条消息，检查 compact 是否触发（查 `agent_v2_session.summary_text`）。compact 失败时 `summary_text` 为空，但 history 仍会正常带上。

**Q：`spawn_subagent` 报 `subagent_type_not_allowed`？**
A：父 Agent 的 `ctx.allowed_subagent_types` 白名单不包含这个名字。当前只有走 definition-backed 的 session 会设置这个字段，老 session（ctx 里为 None）不做限制，任何注册的 subagent 都可用。

## 十、开发者文档

- `PLAN.md` — 整体路线图（Phase 0-3）
- `PLAN-phase2.md` — Phase 2 总入口
- `PLAN-rbac.md` / `PLAN-bot-channels.md` / `PLAN-multi-agent.md` — Phase 2.1/2.2/2.3 详设
- `PLAN-agent-runtime-maturity.md` — Phase 2.5 详设（Citation validator / 多轮 / Definition）
- `DESIGN.md` — Phase 1 架构设计（稳定，不再改）
- `STATUS.md` — 会话交接文档，当前进度
- `FORK.md` — 与上游 infiniflow/ragflow 的差异
- `api/agent_v2/tools/README.md` — 工具开发规范
- `test/agent_v2/` — pytest 单元测试
- `test/e2e/baojian_house.md` — 保障房黄金用例
- `scripts/run_baojian_golden.py` — 批量跑分脚本

### 关键目录

```
api/agent_v2/
├── runner.py               # AgentRunner（Phase 2.5.2 接受 history + summary_text）
├── event.py                # SSE 事件类型（+ subagent_*, citation_warning）
├── registry.py             # 工具注册表
├── compactor.py            # Phase 2.5.2 — 历史摘要压缩
├── validators/             # Phase 2.5.1 — Citation Validator
│   ├── evidence_index.py   # 数字抽取 + EvidenceIndex
│   └── citation.py         # validate_citations()
├── definitions/            # Phase 2.5.3 — Agent Definition manifest
│   ├── schema.py           # AgentDefinition dataclass
│   ├── registry.py         # pkgutil 自动注册
│   └── built_in/           # 6 supervisor + 2 subagent
├── tools/                  # MCP 工具
│   ├── base.py             # ToolContext + ContextVar
│   ├── rag_retrieve.py
│   ├── rag_read_doc.py
│   ├── rag_list_docs.py
│   ├── rag_graph_query.py
│   └── spawn_subagent.py   # Phase 2.5.3 接受 subagent_type
└── templates.py            # M1.6 硬编码 6 模板（逐步迁到 definitions/）
```

## 十一、协议

Apache 2.0，随 RAGFlow 上游。
