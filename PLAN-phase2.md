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

| 阶段 | 目标 | 交付物 | 预计耗时 |
|---|---|---|---|
| **P2.1** | 数据集 RBAC + 审计 | 新表 `dataset_access` / `access_audit_log`，新端点，前端"成员"Tab | 2 天 |
| **P2.2** | 飞书机器人 | 新表 `bot_channel` / `bot_conversation_map`，webhook 适配器，前端"渠道"页 | 3 天 |
| **P2.3** | Multi-Agent | 新工具 `spawn_subagent`，新表 `agent_v2_subagent_trace`，前端子 Agent 可视化 | 2 天 |
| **P2.5** | Agent Runtime 成熟化 | Citation validator + 多轮上下文 + Agent definition manifest；参考 `vendor/claude-code-ref` | 9 天 |

每一阶段都是自成闭环的，不依赖后一阶段。

**Phase 2.5 为什么单列**：Phase 2.1/2.2/2.3 做完后，外部评审指出三个结构性缺口（答案可信度没有硬保障、session 模型侧实际无状态、Agent/Tool 定义硬编码），决定能否从"内部 demo"走到"付费客户能买"。详见 `PLAN-agent-runtime-maturity.md`。

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
| `STATUS.md` | 会话交接文档，实时进度 |
| `DESIGN.md` | Phase 1 Agent v2 架构（稳定，不再改）|
| `FORK.md` | 与上游的差异 |
