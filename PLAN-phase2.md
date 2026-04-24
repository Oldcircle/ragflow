# Phase 2 — 企业化与 Agent 能力升级

> Phase 1.7 完成后，系统前端已有完整「知源 · 企业知识库」外观。Phase 2 聚焦三件能真正让这个项目作为 ToB 企业产品跑起来的事：**访问控制**、**IM 渠道**、**多 Agent 协作**。

---

## 一、为什么是这三件

三项都来自 survey 出的真实缺口，不是虚的：

1. **数据集 RBAC**（`PLAN-rbac.md`）：当前只有 `me / team` 两档权限，部门墙、审计、文档密级全部缺失。更严重的是：
   - `async_ask()` / Agent v2 `session create` / `rag_retrieve` 工具**不检查** `kb_ids` 是否属于当前用户
   - `KnowledgebaseService.accessible(kb_id, user_id)` 帮手方法存在但没被调用在核心路径
   - 没有任何访问审计

   这是**数据安全硬门槛**，不做就不能对外卖。

2. **IM 机器人渠道**（`PLAN-bot-channels.md`）：企业员工 80% 的内部咨询流量在飞书/钉钉/企微。RAGFlow 原版只支持 iframe embed 和 APIToken 直接调用，没有 IM 机器人适配层。
   - 参考稿：`vendor/openclaw/extensions/feishu/` 的 adapter 模式（HMAC 签名、URL challenge、顺序队列、消息去重）
   - 复用 RAGFlow 已有的 webhook 基建（`POST /v1/webhook/<agent_id>` 已有鉴权/限流/IP 白名单）和 `APIToken.beta` 模式

3. **Multi-Agent 协作**（`PLAN-multi-agent.md`）：当前 Agent v2 只是 Supervisor + 4 个 RAG 工具。复杂任务（「对比 3 家公司财报 → 综合评估 → 导出到飞书群」）一个 Agent 做不完。
   - 参考稿：`vendor/claude-code-ref/packages/builtin-tools/src/tools/AgentTool/` 的子 Agent 模式
   - 核心原理：主 Agent 用一个 `spawn_subagent` 工具 → 子 Agent 用独立 context 完成聚焦任务 → 返回综合文本
   - 我们已用 Claude Agent SDK，它原生支持嵌套 query，直接可落地

---

## 二、路线图

| 阶段 | 目标 | 交付物 | 状态 |
|---|---|---|---|
| **P2.1** | 数据集 RBAC + 审计 | 新表 `dataset_access` / `access_audit_log`，新端点，前端"成员"Tab | ✅ 完成 |
| **P2.2** | 飞书机器人 | 新表 `bot_channel` / `bot_conversation_map`，webhook 适配器，前端"渠道"页 | ✅ 完成 |
| **P2.3** | Multi-Agent | 新工具 `spawn_subagent`，新表 `agent_v2_subagent_trace`，前端子 Agent 可视化 | ✅ 完成 |
| **P2.5** | Agent Runtime 成熟化 | Citation validator + 多轮上下文 + Agent definition manifest；参考 `vendor/claude-code-ref` | ✅ 完成（2026-04-23） |
| **P2.6** | 文档运营工具 + 自维护 KB Agent | 18 MCP 工具 / 6 supervisor + 4 subagent / AUDIT-claude-code-alignment 对齐 / runtime plan gate（v0.4）+ 执行闭环（v0.6） | ✅ v0.6 完成（2026-04-23） |

每一阶段都是自成闭环的，不依赖后一阶段。

**Phase 2.5 为什么单列**：Phase 2.1/2.2/2.3 做完后，外部评审指出三个结构性缺口（答案可信度没有硬保障、session 模型侧实际无状态、Agent/Tool 定义硬编码），决定能否从"内部 demo"走到"付费客户能买"。详见 `PLAN-agent-runtime-maturity.md`。

**Phase 2.6 后续迭代**：v0.1 首发 6 写工具 + 2 交互工具 + sub_archivist；v0.2
加 4 自省 / 总结工具 + sub_librarian（Agent 能自维护 KB 而非只执行用户命令）；
v0.3 全面对齐 Claude Code 设计哲学（8 段式英文 prompt / 工具 description 英文化
/ supervisor `tools=SUPERVISOR_TOOLS` 死锁架构分离）；v0.4 真 runtime plan
gate + 工具元数据注解 + 写工具 `next_steps` 提示；v0.5 searchHint + MCP 协议
原生 annotations + 历史保留 tool_use/tool_result + per-subagent 模型路由；
v0.6 plan 执行闭环（`get_pending_plan` 工具 + `[step K/N done]` 标记）。
详见 `PLAN-doc-ops.md` + `AUDIT-claude-code-alignment.md`。

**Phase 2.5 落地 commits**：
- P2.5.1 Citation Validator — `540bfb91f`
- P2.5.2 多轮上下文 + Compact — `86fb8e867`
- P2.5.3 Agent Definition Manifest — `c921ea729`

---

## 三、技术约束与不做清单

- **不破坏上游 schema**：只加新表，不改 `knowledgebase` / `user` / `tenant` 等上游表的结构
- **不搞自己的 Agent 框架**：Multi-Agent 走 Claude Agent SDK 的嵌套 `query()` 方式
- **不做全企业级 RBAC**（deferred to Phase 3）：不做字段级权限、文档密级、SAML/LDAP；P2.1 只做知识库级三角色
- **不做钉钉/企微 v1**（deferred）：结构层预留适配器接口，飞书先跑通
- **不做 Agent-to-Agent handoff**（deferred to Phase 2.5）：P2.3 只做父→子单向委派

---

## 四、成功指标（Phase 2 结束时）

**技术侧**：
- `async_ask` / Agent v2 create session / `rag_retrieve` 三处 KB 访问路径 100% 做访问检查
- 飞书机器人端到端：飞书群里 @ 机器人 → 进入 Agent v2 session → 带引用的回答回传
- 至少 1 个保障房场景 demo 用 Multi-Agent 跑通：「列出所有申请条件相关文档 → 子 Agent 分别提取 → 主 Agent 综合」

**产品侧**：
- 可以把一个全新用户邀请进某个知识库、授予 contributor 角色，该用户能上传文档但不能改权限
- 飞书企业管理员在 OpenClaw Admin 风格的后台填 app_id / app_secret 就能开通机器人
- 子 Agent 执行过程在前端工具调用侧栏可点开展示完整对话链

---

## 五、活跃文档

| 文档 | 作用 |
|---|---|
| `PLAN.md` | 项目总体路线（Phase 0/1/2/3）|
| `PLAN-phase2.md`（本文件）| Phase 2 入口 |
| `PLAN-rbac.md` | P2.1 数据集 RBAC 详细设计 |
| `PLAN-bot-channels.md` | P2.2 IM 机器人渠道详细设计 |
| `PLAN-multi-agent.md` | P2.3 Multi-Agent 详细设计 |
| `PLAN-agent-runtime-maturity.md` | P2.5 Agent Runtime 成熟化详细设计 |
| `PLAN-doc-ops.md` | P2.6 文档运营工具 + 自维护 KB Agent 设计 |
| `AUDIT-claude-code-alignment.md` | P2.6 v0.3/v0.4 对齐 Claude Code 的 20 维度审计 + 10 个未提发现 |
| `FINDINGS-phase-26-v02-live.md` | P2.6 v0.2 活体测试的 3 个架构级 bug 修复记录 |
| `STATUS.md` | 会话交接文档，实时进度 |
| `DESIGN.md` | Phase 1 Agent v2 架构（稳定，不再改）|
| `FORK.md` | 与上游的差异 |
