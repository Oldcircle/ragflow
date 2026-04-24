# Phase 2.6 — Document Operations (Agent write-capabilities)

> **定位**：把 Agent v2 从"只读的知识库顾问"升级到"能执行语义级文件操作的知识库运营助手"。目标不是给 Agent bash，而是**按 KB 业务语义**暴露写工具——移动 / 归档 / 打标签 / 重命名 / 重解析 / 从 URL 入库 / 新建分类桶。
> **起始日期**：2026-04-24
> **范围**：新增工具 + RBAC 写门禁 + 审计扩展 + 新 Agent Definition `sub_archivist` + `ask_user_question` / `agent_plan` 两个交互工具
> **参考**：`~/Opensource/vendor/claude-code-ref/packages/builtin-tools/src/tools/`

---

## 一、为什么不抄 bash

Claude Code 给 Agent 开了 `BashTool + FileReadTool + FileEditTool + FileWriteTool`，配 worktree / permission mode / command allowlist。看起来通用，但套到企业知识库是**错配**：

- KB 场景里"操作文件"的真实含义是：**这份合同该归到法务库 / 这批文档标成「已过期」 / 这个 PDF 重跑一下新解析器**——**全都是 KB 业务语义**，不是 POSIX 操作
- 裸 bash 的攻击面（命令注入 / 路径逃逸 / 软链绕沙箱 / 子进程 fork）在 ToB 合规审计里是红线
- RAGFlow 已经有完整的 `DocumentService` / `KnowledgebaseService` / `DocMetadataService`，UI 就是直调这套——Agent 应该走**同一条规范化路径**，而不是绕过业务层直接操 DB / 文件

结论：**以语义工具为主，不引入 bash**。如果未来真出现"Agent 需要跑 pandoc / ocrmypdf"这种场景，再加**命令模板白名单**式的 `exec_preset`，**不是裸 bash**。

---

## 二、四条决策（已定）

| # | 决策 | 选项 | 结论 |
|---|---|---|---|
| 1 | 谁能拿写工具 | 宽松 vs 严格 | **严格**：supervisor 默认只读；写工具集中到 `sub_archivist` subagent，由 supervisor 的 `allowed_subagent_types` 显式授权 |
| 2 | 破坏性操作门槛 | confirm 参数 vs 人工审批队列 | **v1 用 confirm=true + 软删 7 天 grace**；人工审批排 Phase 3 任务生命周期 |
| 3 | 跨 KB 移动的权限 | 单边 vs 双边 | **双边**：源 KB 和目标 KB 都需 `CONTRIBUTOR+` |
| 4 | 自主写的边界 | 主动 vs 仅指令 | **仅明确指令**：v1 Agent **只在用户明确说"帮我归档 / 重命名 / 打标签"时**调写工具；可以先建议后等用户确认 |

---

## 三、从 claude-code-ref 挑什么、不挑什么

参考树：`~/Opensource/vendor/claude-code-ref/packages/builtin-tools/src/tools/`。

| 工具 | 抄不抄 | 原因 |
|---|:-:|---|
| `AgentTool` | ✅ 已抄（`spawn_subagent`，P2.3） | 子 Agent 委派 |
| **`AskUserQuestionTool`** | ✅ 本 Phase 新抄 | 多选澄清；KB 场景非常有用（"归到法务库还是合规库？"） |
| **`EnterPlanModeTool` + `ExitPlanModeTool`** | ✅ 本 Phase 合并抄成单工具 `submit_plan` | 让 Agent 在执行前先出计划，用户审批后再动手；破坏性操作之前天然的人工审批 |
| `BashTool` / `FileReadTool` / `FileEditTool` / `FileWriteTool` | ❌ | 见第一节理由 |
| `EnterWorktreeTool` / `ExitWorktreeTool` | ❌ | 需要文件系统隔离，KB 场景不需要 |
| `GlobTool` | ❌（暂） | 可用 `rag_list_docs` 的 filter 覆盖 90% 用例；真缺再加 |
| `GrepTool` | ❌ | `rag_retrieve` 是语义版，更符合 KB 语义 |
| `MonitorTool` | ❌（暂） | 等 Phase 3 任务生命周期 |
| `PushNotificationTool` | ❌（暂） | 同上，需要异步任务框架 |
| `TodoWriteTool` / `TaskCreate*` | ❌ | Claude Code 自用，KB Agent 用 `submit_plan` 替代 |
| `WebBrowserTool` / `WebFetchTool` / `WebSearchTool` | 🟡 暂不加，但考虑 | `doc_upload_from_url` 提供了轻量拉取能力；完整 web 访问留到后续 |
| `MCPTool` / 外部 MCP server | ❌（本 Phase） | 后续 Phase 扩展点 |

---

## 四、工具清单 + 安全属性

按安全阶梯从低到高，**按这个顺序实现 + 发布**：

| 顺序 | 工具 | 副作用 | 可逆性 | 权限 | 审计 action |
|:-:|---|---|---|---|---|
| 1 | **`doc_tag`** | 增/删 meta 标签 | ✅ 完全可逆（重调一次 remove） | CONTRIBUTOR+ | `kb.doc.tag` |
| 2 | **`doc_rename`** | 改 Document.name | ✅ 可逆（旧名写进 audit metadata） | CONTRIBUTOR+ | `kb.doc.rename` |
| 3 | **`kb_create`** | 建新 KB（纯 additive） | ✅ 空 KB 可删 | tenant 成员 + kb_max 配额 | `kb.create` |
| 4 | **`doc_archive`** | 跨 KB 移动 Document | 🟡 需 `doc_archive(..., reverse=true)` 回滚 | **双边** CONTRIBUTOR+，embedding 兼容 | `kb.doc.archive` |
| 5 | **`doc_reparse`** | 清 chunks 重跑解析 | 🟡 原 chunks 被清（但原 blob 保留） | CONTRIBUTOR+ | `kb.doc.reparse` |
| 6 | **`doc_upload_from_url`** | 从 URL 下载 + 上传 Document | ✅ 新增可删（但占存储） | CONTRIBUTOR+，URL allowlist | `kb.doc.upload` |
| 7 | **`ask_user_question`** | 无副作用，等用户选择 | ✅ | 任何 session | `agent_v2.ask_user` |
| 8 | **`submit_plan`** | 无副作用，等用户审批 | ✅ | 任何 session | `agent_v2.plan_submit` / `agent_v2.plan_approve` / `agent_v2.plan_reject` |

**明确 v1 不做**：
- ❌ `doc_delete`（硬删）— 等人工审批机制上线
- ❌ `doc_delete_soft`（软删）— 未来可做，但先不带；tag 一个 `archived=true` + 移到归档 KB 能覆盖
- ❌ `kb_delete` — 极破坏，管理员后台做
- ❌ `doc_edit_content`（改文档正文）— 改 blob 违反"知识库存的是原文"承诺，不做
- ❌ `doc_split` / `doc_merge` — 高级运维，后续

---

## 五、基础设施

### 5.1 代码结构

```
api/agent_v2/tools/
  doc_ops/
    __init__.py                      # 导出 6 个写工具
    _common.py                       # @require_kb_write 装饰器 + write_audit + idempotency
    doc_tag.py
    doc_rename.py
    doc_archive.py
    doc_reparse.py
    doc_upload_from_url.py
    kb_create.py
  ask_user_question.py               # 交互：问用户
  submit_plan.py                     # 交互：审批 plan
```

### 5.2 `@require_kb_write` 装饰器

```python
def require_kb_write(kb_id_getter, min_role=DatasetRole.CONTRIBUTOR, action: str):
    """
    统一所有 write 工具的门禁 + 审计:
    1. 从 ctx.user_id + kb_id 做 RBAC 检查
    2. 失败直接写 deny 审计并返 mcp_json_response({"error": "no_access", ...})
    3. 成功调工具，把返回包进 allow 审计（含 metadata）
    4. 异常把异常包进 deny 审计并重抛（Runner 翻译成 error event）
    """
```

### 5.3 idempotency（防重复执行）

工具入参带可选 `idempotency_key`；若 90 秒内看到重复 key + 同参数 → 返回上次结果而不是执行两次。放 Redis (`agent_v2:idem:<key>`) TTL 90s，兜底内存 LRU（参考 P2.2 dedup 实现模式）。

### 5.4 审计扩展

`access_audit_log.metadata` JSON 里为写操作多记几个字段：
```json
{
  "op": "kb.doc.archive",
  "source_kb_id": "...",
  "target_kb_id": "...",
  "doc_id": "...",
  "doc_name_at_op": "...",
  "reversible_hint": "call kb.doc.archive with source/target swapped"
}
```
这样"谁在 X 时改了 Y" 能直接定位 + 提供 undo 线索。

### 5.5 配额钩子

新写工具按情况扣：
- `kb_create` → `kb_count` 实时计数，触达 `kb_max` 拒
- `doc_upload_from_url` → 算新增 doc + 文件大小，触达 `doc_max` / `storage_max`（后者 Phase 3 再加）拒
- `doc_archive` / `doc_reparse` / `doc_tag` / `doc_rename` → 不扣配额（不改增量）

---

## 六、`sub_archivist` Agent Definition（关键）

新文件 `api/agent_v2/definitions/built_in/sub_archivist.py`：

```python
DEFINITION = AgentDefinition(
    name="sub_archivist",
    version="1.0",
    description="知识库运营助手：把文档归类、打标签、重命名、归档、重解析、入库",
    when_to_use=(
        "用户明确要求对知识库做整理工作时派我。例如："
        "『把合同类文档归到法务库』、『给这批政策文档打上「2024 最新」标签』、"
        "『这个 URL 的白皮书加进行业库』、『这份 PDF 重跑 DeepDoc 解析器』。"
        "我不做检索——那交给其他 subagent 或 supervisor 自己。"
        "我不做破坏性操作（删除、清空）——遇到请让用户走管理员后台。"
    ),
    kind="subagent",
    category="ops",
    system_prompt=ARCHIVIST_SYSTEM_PROMPT,
    tools=[
        "doc_tag", "doc_rename", "kb_create",
        "doc_archive", "doc_reparse", "doc_upload_from_url",
        "ask_user_question", "submit_plan",
        # 可读自己刚改的
        "rag_list_docs", "rag_read_doc",
    ],
    max_turns=15,
    max_budget_usd=0.3,
    citation_enforce="off",  # archivist 不产 [N] 引用答复
    can_spawn_subagents=False,
)
```

Supervisor 侧：`supervisor_baozhang` / `supervisor_generic_policy` / `supervisor_internal_wiki` 的 `allowed_subagent_types` 追加 `"sub_archivist"`。

---

## 七、`ask_user_question` 协议

参考 `AskUserQuestionTool.tsx`。输入：
```json
{
  "question": "归档到哪个 KB？",
  "header": "目标库",
  "options": [
    {"label": "法务知识库", "description": "合同、协议、法规"},
    {"label": "合规知识库", "description": "行业规范、内控"},
    {"label": "其他（自定义）", "description": "用户输入自由文本"}
  ],
  "multi_select": false
}
```

协议：
1. Agent 调 `ask_user_question` → 工具**不立即返回**，而是 emit SSE 事件 `ask_user_question`，然后**挂起当前 turn**
2. 前端收到事件后渲染多选卡片，用户点选/填自由文本
3. 前端把选择作为一条**特殊 user message**（带 `in_response_to=<tool_use_id>`）回发
4. 后端把响应作为 tool_result 塞回 Agent 的消息流，Agent 继续

**关键决策**：v1 不实现真异步——Agent run 里**同步**等前端响应（设 60s 超时；超时则工具返 `{"cancelled": true}` 让 Agent 降级）。真异步需要 Phase 3 任务生命周期。

### 为什么不抄完 Claude Code

- 去掉 `preview` 字段（HTML 预览 KB 场景用不到）
- 去掉 channel 分发（KB Agent 场景没 Slack/Discord 多渠道）
- 选项数 2-4 固定
- multiSelect 保留（打标签场景需要）

---

## 八、`submit_plan` 协议

简化版（合并 EnterPlanMode + ExitPlanMode 为单步），专为"破坏性操作前审批"设计。

输入：
```json
{
  "title": "将过期合同归档到法务-过期库",
  "steps": [
    "在法务-过期库里新建分类文件夹",
    "把 12 份标记为 expired=true 的合同从法务库移过去",
    "给新位置的文档统一打 archived_at=2026-04-24 标签"
  ],
  "affected_resources": [
    {"kind": "kb", "id": "law-active", "action": "source"},
    {"kind": "kb", "id": "law-expired", "action": "target"},
    {"kind": "doc_count", "value": 12, "action": "move"}
  ],
  "risk_level": "medium",
  "estimated_cost_usd": 0.05,
  "reversible": true,
  "reversible_hint": "Each archive can be reversed with doc_archive(reverse=true)"
}
```

协议：
1. Agent 调 `submit_plan` → 工具挂起，emit SSE `plan_submitted`
2. 前端渲染计划审查卡片（步骤列表 + 影响资源 + 风险等级 + 估价）
3. 用户：Approve / Reject / Request Changes
4. 响应回流，Agent 根据结果继续/终止/调整

**gating 规则**（Phase 2.6 v0.4 已落地 ✅）：

实际实现比原计划更保守——**全部** `@require_kb_write` 装饰的写工具默认受 gate
管辖（除低风险 `doc_create_note` 显式 `plan_gated=False`），不再按工具类别开列表。

两层 gate：

1. **同轮锁** — `ctx.plan_submitted_this_turn`。Agent 在一轮内调过 `submit_plan`
   之后，同轮后续任何写都立刻被拒（防止 Agent 提完计划就自作主张动手）
2. **跨轮状态** — `AgentV2Session.pending_plan_status` 列（`waiting` /
   `approved` / `rejected` / `request_changes` / NULL）。状态由 HTTP 端点
   解析用户消息前缀 `[plan approved\|rejected\|request changes]`（中文：
   `[计划批准\|拒绝\|修改]`）自动 transition。只有 `approved` 或 NULL 放行；其他
   态写工具返 `error: plan_gate` + 审计写 deny。1h TTL 自动清

相关代码：
- `api/db/db_models.py::AgentV2Session` — 3 列 nullable schema（向后兼容老 session）
- `api/db/services/agent_v2_service.py::AgentV2SessionService.{set,transition,clear,get}_pending_plan`
- `api/agent_v2/plan_decision.py::parse_plan_decision` — 前缀识别（语言无关）
- `api/agent_v2/tools/doc_ops/_common.py::_plan_gate_check` — gate 本体
- `api/agent_v2/tools/submit_plan.py` — 发 plan 时同步落 DB + 设 ctx flag
- `api/apps/agent_v2_app.py::send_message` — 入口解析前缀 / 注入 runner / 收尾清 stale

---

## 九、实施顺序（我今晚按这个走）

| Step | 内容 | 预计 |
|---|---|---|
| S1 | 写 `PLAN-doc-ops.md`（本文件） | 20 min |
| S2 | 基础 `doc_ops/_common.py`（RBAC 装饰器 + 审计 + idempotency） | 30 min |
| S3 | `doc_tag` + 单测 | 25 min |
| S4 | `doc_rename` + 单测 | 20 min |
| S5 | `kb_create` + 单测 | 30 min |
| S6 | `doc_archive` + 单测 | 45 min |
| S7 | `doc_reparse` + 单测 | 25 min |
| S8 | `doc_upload_from_url` + 单测 | 45 min |
| S9 | `ask_user_question` 工具 + SSE 事件 + 前端钩子 | 60 min |
| S10 | `submit_plan` 工具 + SSE 事件 + 前端钩子 | 45 min |
| S11 | `sub_archivist` definition + supervisor 打通 | 30 min |
| S12 | 把新工具注册进 `registry.py` + supervisor `resolve_tools` 更新 | 10 min |
| S13 | `scripts/tob_acceptance.py` 新增 S20–S27 无 LLM 检查 | 30 min |
| S14 | 全套 `pytest test/agent_v2/` + `ruff check` + `tob_acceptance.py --live-llm` | 20 min |
| S15 | `STATUS.md` / `CLAUDE.md` 收尾 | 15 min |
| **合计** | | ~7 h |

---

## 十、测试矩阵

| 测试文件 | 覆盖 |
|---|---|
| `test/agent_v2/test_doc_ops_common.py` | `@require_kb_write` 装饰器 + audit + idempotency 五条路径（allow / deny-rbac / deny-exception / 成功含 audit metadata / idem hit 返旧结果） |
| `test/agent_v2/test_doc_tag.py` | add/remove/set 三种 operation；不存在的 doc 拒；跨 tenant doc_id 拒 |
| `test/agent_v2/test_doc_rename.py` | 合法重命名；空名拒；非法字符拒；旧名入 audit metadata |
| `test/agent_v2/test_kb_create.py` | 正常建；kb_max 达到时拒；创建者成为 OWNER |
| `test/agent_v2/test_doc_archive.py` | 跨 KB 移动；双边权限；embedding 不兼容时拒；reverse 参数正确回滚 |
| `test/agent_v2/test_doc_reparse.py` | 正常重解析；old_parser 入 audit metadata |
| `test/agent_v2/test_doc_upload_from_url.py` | http/https 通过；file:// 拒；max size 50MB 拒；name 冲突自动 duplicate_name |
| `test/agent_v2/test_ask_user_question.py` | 挂起路径（mock 异步响应）；超时降级；选项数校验 2-4；multi_select 正向负向 |
| `test/agent_v2/test_submit_plan.py` | 挂起路径；approve/reject/request_changes 三态；步骤数 1-10 校验 |
| `test/agent_v2/test_sub_archivist.py` | registry 加载 + supervisor 能 spawn + subagent 拿到正确 tool 集 |
| `scripts/tob_acceptance.py` 新增 | S20 doc_tag 往返；S21 doc_rename；S22 kb_create + quota；S23 doc_archive + 反向；S24 ask_user_question 事件；S25 submit_plan 事件；S26 sub_archivist 结构；S27 URL 下载拒不合规 scheme |

目标：**pytest 加 70+ 用例，acceptance 加 8 个检查**。

---

## 十一、风险 + 回退

| 风险 | 缓解 |
|---|---|
| `doc_archive` 移动过程中 ES chunk 跨 index 搬迁失败 → 半成品 | 先校 embedding / tenant 匹配；失败走事务回滚；失败记审计 `state=partial` 标红 |
| `doc_upload_from_url` SSRF（内网探测） | URL scheme 只允 http/https；resolve 到 IP 时 block RFC1918 / link-local / localhost；`Content-Length` 硬上限；`X-Forwarded-For` 不 forward |
| Agent 在多轮同 session 里重复调 `doc_archive` 把同一个 doc 跨 KB 来回倒 | idempotency key（`(op, doc_id, target_kb)` 90s 内哈希）+ 审计看得到 |
| `ask_user_question` / `submit_plan` 阻塞 SSE 太久 | 60s 超时后工具返 `cancelled`；Agent system prompt 里写明"超时请改降级"|
| `sub_archivist` 绕开 `submit_plan` 直接调破坏工具 | **v0.4 已修**：`@require_kb_write` 跑时 gate，ctx + DB 双层校验，绕不过（详见 §8）|
| 和上游 RAGFlow merge 冲突 | 所有新代码都放在 `api/agent_v2/tools/doc_ops/` 独立目录；DB 不改上游 schema |

---

## 十二、进阶（后续）

- `ExecPresetTool`：命令模板白名单（`pandoc_to_pdf(doc_id)` / `ocrmypdf(doc_id)` / `split_pdf(doc_id, pages)`），每个模板都是事先审过的单命令
- `MonitorTool` / `PushNotificationTool`：长任务后台化 + 完成通知（Phase 3 任务生命周期）
- `doc_delete` / `kb_delete`：走 `submit_plan` 审批 + 管理员复核（Phase 3）
- Web 端操作预览 + undo 栈（基于审计 metadata 做 "Revert last action" 按钮）
- 批量操作：`doc_batch(operations=[...])` 一次多个操作 + 事务

---

## 十三、缺口分析：通用 KB Agent 愿景 vs 当前实现（2026-04-24 v0.2）

用户在 v0.1 落地后指出：**光命令式写工具不够**，目标是"Agent 能自己维护 KB
形态 + 自己总结笔记 + 通用 KB agent"。按这个愿景重新审视 claude-code-ref，
找到的 7 个结构性缺口 + 对应要做的工具：

### 缺口清单

| # | 缺口 | 愿景对应 | Claude Code 参考模式 |
|---|---|---|---|
| G1 | Agent 不能**生成内容并入库** | "自己总结笔记" = Agent 写 Markdown 然后作为新 Document；现有只能 from URL | `FileWriteTool` 语义版（我们只做**文档级**，不做通用 FS） |
| G2 | 没有**结构化体检** | "维护 KB 形态" = 知道"这个库里什么该动"；`rag_list_docs` 是分页读不是 audit | `GlobTool` + `GrepTool` + `SkillTool` 的 discovery batch |
| G3 | 没有**快速健康快照** | 周期巡检要轻量；full audit 太重 | `BriefTool` / `ListPeersTool` 的 "temperature check" |
| G4 | 反思自己操作的能力弱 | Agent 做完批量后要能核对；当前只能依赖用户去审计页看 | `TaskGetTool` / `TaskOutputTool` — 读自己的 task history |
| G5 | 工具 `searchHint` 缺失 | 决定**何时**用哪个工具，不是 what；当前 description 大多只写 what | Claude Code 每个工具都有 `searchHint` 字段 |
| G6 | 没有跨 session memory | "我上周整理过这个 KB，已经打了 archive 标签" 这种持久性知识 | Claude Code `memdir/` persistent memory |
| ~~G7~~ | ~~`submit_plan` 审批后没有批量执行闭环~~ | **v0.6 已落地**：`AgentV2Session.pending_plan_body` + 新工具 `get_pending_plan` + sub_archivist v1.3.0 的 `[step K/N done]` 标记 | — |

### Tier 1 — 本次要做（最小支撑愿景的 4 个新工具）

| 工具 | 解决缺口 | 关键设计 |
|---|---|---|
| **`doc_create_note`** | G1 | Agent 传 ``title + markdown_body + target_kb_id + tags?``；我们内部把 Markdown 当 ``.md`` 文件塞进 `FileService.upload_document` 和 UI 上传完全一致的路径；解析后自动在 Chunks 里可检索；audit `kb.doc.note_create`。**关键**：`content_hash` dedup 要启用——防止 Agent 重复生成同样的笔记 |
| **`kb_audit`** | G2 | 读 `Document` + `access_audit_log`，返回结构化报告：`{by_parse_status, stale_docs, duplicate_candidates, unparsed_docs, top_tags, totals}`。**关键**：pagination-safe（不返全量文档 ID，只返 counts + top-N 样本） |
| **`kb_stats`** | G3 | `kb_audit` 的轻量版；只返 `{doc_count, chunk_count, token_num, size_bytes, embd_id, last_update_at, oldest_doc_at, newest_doc_at}`。**关键**：< 50ms 响应 |
| **`doc_list_recent_changes`** | G4 | 查 `access_audit_log` where `tenant_id=X and resource_type in (knowledgebase, agent_v2_session) and action LIKE 'kb.%' and create_time > now-{window}`；返回按时间倒序的动作流。**关键**：带 page/limit，别一次返太多 |

### Tier 2 — 下一轮（不在本次范围）

- `doc_batch(operations=[...])` 一次提交多个 ops + 事务（G7 部分解决）
- `ExecPresetTool` 命令白名单（pandoc / ocrmypdf）
- Agent 跨 session memory（memdir 模式；可能需要新 `agent_memory` 表）
- Plan execution loop：approve 后自动按步骤执行并回报

### Tier 3 — 很久以后

- `MonitorTool` / `PushNotificationTool`（Phase 3 任务生命周期）
- `doc_delete` / `kb_delete` 人工审批队列

### Sub-agent 重切分（本次做）

用户愿景里 Agent 要**又会改又会看又会总结**。当前 `sub_archivist` 把"改"和"看/总结"混在一起——Claude Code 设计哲学是一 subagent = 一心智模式。重切：

| Subagent | 职责 | 工具 |
|---|---|---|
| **`sub_archivist`**（保留） | **改**：打标签、重命名、跨 KB 移动、重解析、从 URL 入库、建 KB | `doc_tag`, `doc_rename`, `doc_archive`, `doc_reparse`, `doc_upload_from_url`, `kb_create` + `rag_list_docs`, `rag_read_doc` + `ask_user_question`, `submit_plan` |
| **`sub_librarian`**（新增） | **看 + 总结 + 写笔记**：体检 KB、发现陈旧文档、找重复、总结成 Markdown 新笔记入库 | `kb_audit`, `kb_stats`, `doc_list_recent_changes`, `doc_create_note` + `rag_retrieve`, `rag_list_docs`, `rag_read_doc` + `ask_user_question`, `submit_plan` |

两者都能派；supervisor 根据用户意图（整理 vs 研究 vs 混合）选。

### 设计哲学对齐（抄 Claude Code 的形，不抄字面）

- **`searchHint` 必加**：每个工具 MCP description 的第一句必须是"**WHEN**"，不是 what
- **noop 识别**：工具必须检测"这次调用不会改变状态"并返 `status=noop` + reason（防 Agent 白烧 token）
- **可逆线索**：每个写工具的 audit metadata 必带 `reversible_hint`（我已经做到了，继续坚持）
- **幂等 key 默认打开**：写工具若 90s 内同参数重复入，返缓存而非执行（已实现，继续用）
- **状态机反馈**：工具返回里**必须**含 `status` 字段（`ok` / `noop` / `cancelled` / `partial` / `error`），UI 可以按 status 渲染不同颜色

---

---

## 十三、活跃文档更新

`PLAN.md`：Phase 2.6 加入路线图
`CLAUDE.md`：活跃文档清单加 `PLAN-doc-ops.md`
`STATUS.md`：每完成一个工具更新一次

---

## 十四、版本记录

| 日期 | 版本 | 决策 |
|---|---|---|
| 2026-04-24 | v0.1 | 初稿；确定语义工具路径 + 四条默认决策 + 实施顺序 |
