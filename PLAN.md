# RAGFlow Agent 二改 — 总体计划

> **项目代号**：暂定 `RAGFlow Agent Edition`（待正式命名）
> **起始日期**：2026-04-21
> **主负责人**：yb (Oldcircle)
> **基线版本**：`upstream/main` @ v0.24.0

---

## 一、一句话定位

**基于 RAGFlow 改造成 Agent 驱动的企业知识库平台——保留 RAG 全部能力，把交互范式从"每轮自动检索拼 Prompt"改为"Agent 自主决策何时、调用哪些工具"。**

## 二、为什么值得做

### 观察到的痛点
原版 RAGFlow Dialog 模式在"深圳保障房政策"场景下暴露的问题：
- **机械检索**：每轮都强制检索，简单对话也跑 embedding
- **幻觉严重**：召回不全时 LLM 用训练记忆补（实测："本科 45 岁以下 + 专科 35 岁以下"政策里根本没写）
- **无推理链**：不能"先查 A 再查 B"这种多步推理
- **无工具协同**：检索结果拿了就结束，不能 "查不到 → 再 Web Search → 对比 → 综合"

### 市场空位
```
┌────────────────────┬─────────────┬──────────────┬──────────────────┐
│       产品         │   RAG 能力   │  Agent 能力  │      定位        │
├────────────────────┼─────────────┼──────────────┼──────────────────┤
│ Dify / Coze        │      弱     │     强       │ Agent 平台        │
│ MaxKB              │      中     │     中       │ 有付费墙          │
│ RAGFlow 原版       │      强     │     中       │ 工程师向 RAG      │
│ RAGFlow Agent Ed.  │     超强    │     强       │ 我们的空位        │
└────────────────────┴─────────────┴──────────────┴──────────────────┘
```

**差异化**：RAG 护城河最深的 Agent 平台 = 最擅长处理企业文档的智能体框架。

## 三、目标用户（分优先级）

1. **P0**：需要"懂文档"的企业 Agent 场景
   - 政府/国企：政策咨询、合规顾问
   - 律所：合同分析、法规检索
   - 金融：研报综合、尽调助手
2. **P1**：通用企业知识库升级（从 MaxKB / Dify 迁移）
3. **P2**：开发者自建 Agent（提供 SDK 和模板）

### 首个真实场景（已备好数据）
**深圳保障房政策顾问** — 22 份政策文件已入库，用作 Phase 0/1 验证场景。

## 四、技术选型（已决策，不再讨论）

| 层 | 选型 | 理由 |
|---|---|---|
| 后端语言 | **保留 RAGFlow Python (Flask/Quart)** | 不引入混合栈 |
| 前端 | **保留 RAGFlow React + Vite + shadcn/ui** | 已足够好 |
| Agent Runtime | **Claude Agent SDK (Python 官方)** | 稳定、合规、多模型兼容 |
| 主 LLM | **DeepSeek**（已接入） + Claude/GPT（复杂任务备选） | 成本 + 能力平衡 |
| Embedding | **本地 Ollama bge-m3**（已接入） | 无 API 依赖、中文好 |
| 向量引擎 | **Elasticsearch**（默认） | 未来可切 Infinity |
| 可观测性 | **Langfuse**（RAGFlow 已内置） | 零成本 |

### 明确不选
- ❌ `claude-code-ref / CCB`：Bun/TS CLI 架构错配 + 逆向合规风险
- ❌ 纯 LangGraph：学习曲线 + 社区远不如 Claude Agent SDK 活跃
- ❌ 重写 RAGFlow Java/Go 版：浪费已有成果

## 五、四阶段路线图

### Phase 0 — 验证（本周，3-5 天）

**目标**：用 RAGFlow 现有 `Agent with Tools` 节点搭可用 demo，**不写代码**，校准对"LLM 自主调 RAG"的感觉。

**关键动作**：
- [x] 基础 RAG demo（原版 Dialog 模式）— 已完成，发现幻觉问题
- [ ] 在 Agent 画布用 `Agent with Tools` 节点重建保障房顾问
- [ ] 挂载工具：Retrieval（必挂）、Tavily（可选）、ExeSQL（可选）
- [ ] 写一版严格约束的 System Prompt（禁止外部知识补数字）
- [ ] 3 个对比问题测试：事实类、推理类、防幻觉类
- [ ] 观察 Agent 的工具调用日志（何时调、调几次、对不对）

**产出**：
- demo 可用
- 产品预期对齐（确认"Agent-first"是你想要的）
- 识别 Phase 1 要重点做的能力

**退出标准**：
- 3 个对比问题能给出比 Dialog 模式更靠谱的回答
- 明确 Phase 1 的具体需求

---

### Phase 1 — MVP（2-3 周）

**目标**：Claude Agent SDK 集成，新增"Agent 工作台"入口，可对外演示。

**范围**：
- 新增 Agent v2 模块（不动原有 Dialog/画布）
- 包装 RAGFlow 核心能力为 Tool
- 前端新页面
- 打磨到可商用 demo 水平

**里程碑**：

| 里程碑 | 内容 | 预计 | 实际 |
|---|---|---|---|
| M1.1 | SDK 集成验证（最小跑通：能调一个工具） | Day 3 | ✅ 2026-04-21 |
| M1.2 | 4 个核心 Tool 实现 + 单测 | Day 7 | ✅ 2026-04-21 |
| M1.3 | HTTP/SSE endpoint + Session 持久化 | Day 10 | ✅ 2026-04-21 |
| M1.4 | 前端 Agent 工作台 UI（含工具调用可视化） | Day 14 | ✅ 2026-04-22（采用 Claude Design「知源」稿） |
| M1.5 | 保障房场景端到端跑通 + 调优 | Day 17 | ✅ 2026-04-22（10 题均分 4.8/5） |
| M1.6 | 模型供应商 + 模板系统 + [N] 脚注 | Day 21 | ✅ 2026-04-22 |

**核心 Tools（Phase 1 必做）**：
1. `rag_retrieve(query, kb_id, top_n)` — 知识库检索
2. `rag_graph_query(entity, kb_id)` — GraphRAG 实体查询
3. `rag_list_documents(kb_id)` — 列出知识库所有文档
4. `rag_read_document(doc_id, page_range)` — 读完整文档/指定页

**非目标（Phase 1 不做）**：
- Multi-Agent 协作（Phase 2）
- Trigger / 定时（Phase 2）
- 企业管理功能（Phase 3）
- 移除旧版 Dialog（永不，长期并存）

**退出标准**：
- 保障房顾问 Agent 能回答 10 个复杂问题无幻觉
- 前端可视化清晰展示 Agent 思考链 + 工具调用
- 至少 1 个外部用户测试通过

---

### Phase 2 — 企业化与 Agent 能力升级（进行中）

**目标**：从"前端已产品化的原型"变成"真能给企业跑起来"。聚焦三件：数据集访问控制、IM 机器人渠道、Multi-Agent。

**范围**：
- **P2.1 数据集 RBAC + 审计**：新 `dataset_access` 表（owner / admin / contributor / viewer 四角色）+ 补齐 `async_ask` / `agent_v2 session create` / `rag_retrieve` 三处 KB 访问检查 + 新 `access_audit_log` 表。详见 `PLAN-rbac.md`
- **P2.2 飞书机器人渠道**：参考 `~/Opensource/vendor/openclaw/extensions/feishu/` 的 adapter 模式，新 `bot_channel` + `bot_conversation_map` 表，webhook 入口 + 签名验证 + 顺序队列 + 自动绑定 Agent v2 session。详见 `PLAN-bot-channels.md`
- **P2.3 Multi-Agent**：参考 `~/Opensource/vendor/claude-code-ref/packages/builtin-tools/src/tools/AgentTool/` 的 subagent 模式，新增 `spawn_subagent` 工具 + `agent_v2_subagent_trace` 表，父 Agent 可委派聚焦任务给子 Agent，独立 context 不污染父对话。详见 `PLAN-multi-agent.md`

---

### Phase 2.5 — Agent Runtime 成熟化（✅ 完成 2026-04-23）

**目标**：Phase 2 做完后外部评审指出三个结构性缺口，决定能否从"内部 demo"走到"付费客户能买"。不做通用 agent runtime，做**企业 KB Agent 的成熟化**。

**优先级**（**不等于**外评原文，我们把可信度摆第一）：

| 优先级 | 内容 | 状态 |
|---|---|---|
| **P2.5.1** Citation Validator + 证据链约束 | `[N]` 脚注 + 数值型断言自动校验，`citation_warning` SSE 事件 + 前端警示面板；三档模式 off/warn/strict | ✅ commit `540bfb91f` |
| **P2.5.2** 真正的多轮上下文 | `AgentRunner.run(history=..., summary_text=...)` + `<conversation-history>` 合成 user message + 20 条消息阈值触发后台 compact summary | ✅ commit `86fb8e867` |
| **P2.5.3** Agent Definition Manifest | `AgentDefinition` 声明式 schema + pkgutil 注册表 + 6 supervisor + 2 subagent built-in 定义；`spawn_subagent` 接受 `subagent_type` 参数 | ✅ commit `c921ea729` |

**全程对标 `~/Opensource/vendor/claude-code-ref/`**（`AgentTool/loadAgentsDir.ts` / `runAgent.ts` / `forkSubagent.ts` / `resumeAgent.ts` / `built-in/*.ts`），**但只抄模式不抄代码**—— Bun/TS 代码 Agent 场景和 Python KB Agent 场景不能直搬。

详见 `PLAN-agent-runtime-maturity.md`。

**明确不做**（延到 Phase 3）：
- 任务生命周期从 HTTP 解耦（`agent_task` 表 + worker queue）
- 完整 transcript replay
- 工具 policy hook / permission mode / fork agent

**Phase 2.5 follow-up**（不阻塞、可在后续迭代做）：
- 前端 `NewSessionDialog` 从 `/v1/agent_v2/definition` 端点拉模板替换硬编码 `/template`
- 会话设置抽屉暴露 `history_turn_limit` / `citation_enforce_level` 选项
- 保障房 10 题黄金集用 warn 模式重跑以校准 validator 命中/误报率
- 真机跑一遍追问多轮用例（现有 smoke 只覆盖代码路径，不走 LLM）

---

### Phase 2.6 — 文档运营工具 + 交互工具（✅ 完成 2026-04-24）

**目标**：Agent 从"只读 KB 顾问"升级到"能做语义级文档运营"。不做 bash，不做 POSIX 文件操作；所有工具走 RAGFlow 已有 service 层语义。

| 分层 | 工具 | audit action |
|---|---|---|
| **写 — 低风险** | `doc_tag` / `doc_rename` | `kb.doc.tag` / `kb.doc.rename` |
| **写 — additive** | `kb_create` | `kb.create` |
| **写 — 中等风险** | `doc_archive` / `doc_reparse` | `kb.doc.archive` / `kb.doc.reparse` |
| **写 — 外部内容入库** | `doc_upload_from_url` | `kb.doc.upload` |
| **交互** | `ask_user_question` / `submit_plan` | `agent_v2.ask_user` / `agent_v2.plan_submit` |

**四条默认决策**（详见 `PLAN-doc-ops.md §2`）：
1. Supervisor 默认只读；写工具集中到 `sub_archivist` subagent
2. 破坏性操作用 confirm=true + 软删 grace period；人工审批走 Phase 3 任务生命周期
3. 跨 KB 移动需要源 + 目标**双边** CONTRIBUTOR+
4. Agent 仅在用户**明确指令**时动手；批量前先 submit_plan

**参考 `~/Opensource/vendor/claude-code-ref/`**：`AskUserQuestionTool` / `EnterPlanModeTool` / `ExitPlanModeTool`；抄 schema + 交互协议，去掉 HTML preview / channel 分发。不抄 BashTool / FileReadTool / FileWriteTool / FileEditTool / WorktreeTool（那是代码 Agent 场景）。

详见 `PLAN-doc-ops.md`。

**明确不做**（延 Phase 3）：
- `doc_delete` / `kb_delete` — 等人工审批队列
- `doc_edit_content` — 不改 blob 内容（违反"KB 存原文"承诺）
- 裸 bash / exec — 以后需要再做 `exec_preset` 命令白名单

**非目标（推迟到 Phase 2.5 / 3）**：
- 钉钉 / 企微适配器（预留扩展点，飞书先跑通）
- Trigger（Cron + Webhook 定时任务）
- 垂直模板（保障房 / 法务 / 研报）
- 版本快照 + Langfuse 深度集成
- 字段级 / 文档密级权限

---

### Phase 1.7 — 前端产品化重构（当前）

**目标**：把现有 RAGFlow 原版前端包装成我们自己的企业知识库产品，参考 `design-refs/zhiyuan/`「知源 · 企业知识库」稿统一品牌、导航、首页和主要业务页面，同时保留全部功能。

**范围**：
- 只改前端 UI / UX / 信息架构 / 文案 / design tokens
- 保留现有后端、API、鉴权、数据请求、路由和功能入口
- Agent v2 工作台继续作为核心差异化入口

**分批**：
- P1.7-A：全局品牌外壳、登录页、首页（已完成首批 + sidebar 二轮翻新）
- P1.7-B：Agent / 对话工作台视觉统一（进行中：对话详情页已向 Agent 对齐）
- P1.7-C：知识库 / 文档 / 检索测试 / 图谱页面
- P1.7-D：搜索、Agent 编排、记忆、文件、用户设置收口

详见 `PRODUCT-UI-PLAN.md`。

---

### Phase 3 — 商用化（2-3 个月）

**目标**：ToB 企业能买的产品形态。

- 多租户数据隔离（tenant_id 全链路）
- RBAC 权限（角色、资源、操作）
- 审计日志（所有写操作落库）
- SSO 对接（OAuth2/OIDC 已有壳子，扩到 SAML/CAS/LDAP）
- 计量计费（Token / 调用次数 / 存储）
- 企业管理台（工作空间、成员、配额）

## 六、Non-goals（明确不做）

| 不做 | 原因 |
|---|---|
| 纯 Agent 平台（与 Dify 正面撞） | RAG 是差异化，丢了就没价值 |
| CLI / Desktop 产品 | Web/API 就够覆盖 80% 场景 |
| 非中文优先 | 资源聚焦 |
| 移动端原生 App | H5/PWA 覆盖 |
| 替换 RAGFlow 的 Dialog 模式 | 并存，老用户无迁移成本 |
| 自研模型 | 永远用现成的 |

## 七、风险清单

| 风险 | 等级 | 缓解 |
|---|---|---|
| Claude Agent SDK 还很新、API 可能变 | 中 | 用稳定的 API 表面；抽象层隔离 |
| DeepSeek Function Calling 兼容性 | 中 | 早期用 Claude 原生验证；逐步切 DeepSeek |
| Agent 多轮调用成本失控 | 中 | Token 预算机制 + 早停策略 + Prompt 压缩 |
| 上游 RAGFlow 破坏性更新 | 低 | upstream 定期 merge，`FORK.md` 记录冲突点 |
| 合规（保障房数据敏感性） | 中 | 本地部署、不外传；所有数据走本地模型可选 |
| 工具调用出错（SQL 注入/文件泄露） | 高 | 工具级权限 + Sandbox + 黑名单 |

## 八、成功指标（3 个月后回看）

**技术侧**：
- Agent v2 在保障房场景命中率 > 90%，幻觉率 < 5%
- Agent 端到端响应时延中位数 < 15s
- 上游 merge 冲突平均每次 < 30 分钟

**产品侧**：
- 至少 3 个垂直模板上线
- 至少 1 个外部真实用户持续使用
- 月活可测试用户 ≥ 20

## 九、活跃文档

| 文档 | 定位 |
|---|---|
| `PLAN.md`（本文件） | 总体计划、阶段目标（不频繁改） |
| `PLAN-phase2.md` | Phase 2 路线图总入口 |
| `PLAN-rbac.md` | P2.1 数据集 RBAC + 审计详细设计 |
| `PLAN-bot-channels.md` | P2.2 飞书机器人渠道详细设计 |
| `PLAN-multi-agent.md` | P2.3 Multi-Agent subagent 详细设计 |
| `PLAN-agent-runtime-maturity.md` | Phase 2.5 Agent Runtime 成熟化（Citation validator / 多轮上下文 / Agent definition，参考 `vendor/claude-code-ref`）|
| `PLAN-doc-ops.md` | Phase 2.6 文档运营工具（doc_tag / rename / archive / reparse / upload / kb_create + ask_user_question / submit_plan + sub_archivist）|
| `PRODUCT-UI-PLAN.md` | Phase 1.7 前端产品化（已完成，可归档）|
| `STATUS.md` | 会话交接文档，每次实质进展必更 |
| `DESIGN.md` | Phase 1 Agent v2 架构设计（稳定，不再改） |
| `FORK.md` | 与上游 infiniflow/ragflow 的差异和同步策略 |
| `CLAUDE.md` | 项目说明书（运行命令、约定、首次运行记录） |
| `web/CLAUDE.md` | 前端开发规范（上游自带） |

## 十、版本与决策记录

| 日期 | 版本 | 决策 |
|---|---|---|
| 2026-04-21 | v0.1 | 初稿；确定路径：RAGFlow + Claude Agent SDK，不用 CCB |
| 2026-04-23 | v0.2 | 插入 Phase 2.5（Agent Runtime 成熟化）：Citation validator / 多轮上下文 / Agent definition manifest，全程参考 `vendor/claude-code-ref` |
| 2026-04-24 | v0.3 | 插入 Phase 2.6（文档运营工具）：6 写工具 + 2 交互工具 + sub_archivist subagent + 前端卡片渲染；Agent 正式从 KB-QA 升级到可做语义级文档运营 |
| 2026-04-24 | v0.4 | Phase 2.6 v0.2：+4 自省工具（doc_create_note / kb_audit / kb_stats / doc_list_recent_changes）+ `sub_librarian` subagent；Agent 能自体检 KB、写报告笔记入库、自省操作记录；Claude Code 设计哲学对齐（【WHEN】描述 / status 字段 / noop 检测 / reversible_hint）|
| 2026-04-24 | v0.5 | Phase 2.6 v0.3：系统对齐 Claude Code；10 个 AgentDefinition 的 system_prompt 改英文 + 八段式（Role / Domain / Hard rules / Workflow / Delegation / Tool rules / Output）；17 个 tool description 改英文 + "Use this tool when" 风格；supervisor `tools=SUPERVISOR_TOOLS` 死锁架构分离；`spawn_subagent` 不再父子交集（命名 subagent 可拿父没有的工具）；详见 `AUDIT-claude-code-alignment.md` |
| 2026-04-23 | v0.3 | Phase 2.5 全部完成（commits `540bfb91f` / `86fb8e867` / `c921ea729`）；Phase 2 + 3.1 + 3.2 + 2.5 全数落地，下一批为 Phase 3.3 企业管理台或 P3.2c 钉钉/企微 |
