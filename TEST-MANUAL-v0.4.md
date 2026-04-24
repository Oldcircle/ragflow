# RAGFlow Fork 人工测试清单（v0.4）

> **用途**：Phase 2.6 v0.4 上线前的人工回归测试，覆盖本 fork 相对上游 infiniflow/ragflow 的**全部二次开发内容** + 关键上游回归。
>
> **生成日期**：2026-04-24
> **基线版本**：Phase 2.6 v0.4（真 submit_plan runtime gate + tool annotations + next_steps）
> **使用方式**：按 G1→G12 顺序执行；每条用例完成后勾 `[x]`；失败写在「备注」列。

---

## 目录

- [测试前置](#测试前置)
- [执行顺序](#执行顺序)
- [3 个核心差异化能力（优先测）](#3-个核心差异化能力优先测)
- [G1. 基础设施冒烟](#g1-基础设施冒烟)
- [G2. Agent v2 对话主路径](#g2-agent-v2-对话主路径)
- [G3. 文档运营工具](#g3-文档运营工具)
- [G4. 自省工具 + sub_librarian](#g4-自省工具--sub_librarian)
- [G5. 交互工具 + Plan Gate 执行循环](#g5-交互工具--plan-gate-执行循环)
- [G6. Citation Validator](#g6-citation-validator)
- [G7. 多轮上下文 + Compactor](#g7-多轮上下文--compactor)
- [G8. RBAC + 审计](#g8-rbac--审计)
- [G9. 飞书机器人渠道](#g9-飞书机器人渠道)
- [G10. 租户配额 + 用量 + 限流](#g10-租户配额--用量--限流)
- [G11. 前端产品化](#g11-前端产品化)
- [G12. 上游回归](#g12-上游回归)
- [已被自动化覆盖可 skip 的](#已被自动化覆盖可-skip-的)
- [规模统计](#规模统计)

---

## 测试前置

### 环境变量

```bash
# 必需
export AGENT_V2_DEEPSEEK_KEY="sk-..."        # 或 AGENT_V2_ANTHROPIC_KEY
export RAGFLOW_BOT_CHANNEL_SECRET_KEY="..."  # 飞书 secret 加密 key
export PYTHONPATH="$(pwd)"
export NLTK_DATA="./nltk_data"
```

### 依赖服务

```bash
cd ~/Opensource/forks/ragflow
docker compose -f docker/docker-compose-base.yml up -d   # MySQL / ES / Redis / MinIO
```

### 后端 + 前端启动

```bash
# 后端（macOS 本地开发，绕 launch_backend_service.sh）
nohup .venv/bin/python api/ragflow_server.py > logs/backend.log 2>&1 &

# Task Executor（文档解析 / reparse 需要）
.venv/bin/python rag/svr/task_executor.py 0

# 前端
cd web && nohup npm run dev > ../logs/frontend.log 2>&1 &
```

- 后端：http://localhost:9380
- 前端：http://localhost:9222

### 测试账号准备

为 RBAC / 跨租户测试准备至少 3 个账号：
- `owner@t1` — tenant_A 管理员，OWNER
- `contributor@t1` — tenant_A 成员，CONTRIBUTOR
- `viewer@t2` — tenant_B 成员（跨租户验证）

### 测试数据准备

- 至少 1 个知识库（建议用保障房 22 份政策文档，符合真实场景）
- 至少 1 个空 KB（测 `kb_create` / `doc_upload_from_url`）

---

## 执行顺序

```
G1（冒烟 10min）
  → G2（对话主路径 30min）
  → G3 / G4 / G5 / G6 / G7（工具 + 交互 + 验证，可并行，~2h）
  → G8（RBAC 贯穿验证 30min）
  → G9 / G10（飞书 + 配额，可并行 75min）
  → G11（前端 UI 抽查 60min）
  → G12（上游回归 20min）
```

**单人顺序**：~8-10 小时  |  **3 人并行**：~4.5 小时

---

## 3 个核心差异化能力（优先测）

1. **Plan Gate Runtime 执行循环** → G5-002 ~ G5-004
2. **文档运营工具套件** → G3-001 ~ G3-006 + G4-001 ~ G4-004
3. **企业级审计 + RBAC** → G8-001 ~ G8-004

---

## G1. 基础设施冒烟

### [ ] G1-001 后端启动与依赖连通 — **P0**

- **前置**：Docker 依赖服务 up，env 变量齐
- **步骤**：
  1. `python api/ragflow_server.py`
  2. 观察启动日志
- **预期**：
  - `Uvicorn running on ...` + MySQL/Redis/ES ready
  - 无 CRITICAL，端口 9380 可达
- **备注**：

### [ ] G1-002 前端 Dev Server 启动 — **P0**

- **前置**：Node.js ≥18.20.4 + `npm install` 完成
- **步骤**：
  1. `cd web && npm run dev`
  2. 打开 http://localhost:9222
- **预期**：
  - Vite dev server 就绪（< 5s）
  - 页面加载成功，跳登录页
- **备注**：

### [ ] G1-003 本地登录流程 — **P0**

- **前置**：后端 + 前端运行
- **步骤**：
  1. 访问 http://localhost:9222/login
  2. 输入任意用户名 + 密码
  3. 点登录
- **预期**：
  - 跳首页或 `/agent-chat`
  - localStorage 存 `Authorization` token
- **备注**：

---

## G2. Agent v2 对话主路径

### [ ] G2-001 创建 session + 列出会话 — **P1** 🔴 需 LLM

- **前置**：登录，≥1 个 KB
- **步骤**：
  1. 前端 `/agent-chat` 点"新建对话"，选 KB + model
  2. 创建后列表出现新会话
- **预期**：
  - `AgentV2Session` 表新增一行
  - status='active'，包含 kb_ids / model_config_json
- **备注**：

### [ ] G2-002 单轮对话（无历史） — **P1** 🔴 需 LLM 🟡 需真 KB

- **前置**：session 已建，KB 有内容
- **步骤**：
  1. 发送 "知识库里最近的政策是什么"
  2. 观察 SSE 流
- **预期**：
  - 事件序列：text_delta → tool_call_start(rag_retrieve) → tool_call_end → text_delta → end
  - 答复含脚注 `[1]` `[2]`
  - 消息存 DB（role=user+assistant）
- **备注**：

### [ ] G2-003 多轮对话（历史 + summary） — **P1** 🔴 需 LLM

- **前置**：session 已有 ≥5 条历史
- **步骤**：
  1. 发 "刚才提到的 XX 现在怎样"
  2. 观察 `AgentRunner.run(history=...)` 是否接到历史
- **预期**：
  - 答复能引用历史内容
  - 若超 20 条阈值，`access_audit_log` 记 `agent_v2.compact`
- **备注**：

### [ ] G2-004 Subagent 派出与轨迹记录 — **P1** 🔴 需 LLM

- **前置**：触发 spawn_subagent 的场景（e.g., 让 supervisor 委派 archivist 归档）
- **步骤**：
  1. 发送会触发 spawn_subagent 的指令
  2. SSE 中看 `subagent_start` / `subagent_end` 事件
  3. `GET /v1/agent_v2/session/{id}/subagent`
- **预期**：
  - `agent_v2_subagent_trace` 表有记录：parent_tool_call_id / allowed_tools / status / cost_usd / duration_ms
  - 子 agent 结果能被父 agent 继续推理
- **备注**：

### [ ] G2-005 Plan Gate Runtime 基础检查 — **P1** 🔴 需 LLM

- **前置**：supervisor + sub_archivist 场景
- **步骤**：
  1. sub_archivist 试图直接调 `doc_archive`（无 plan）
  2. 再试 submit_plan 后调 `doc_rename`
- **预期**：
  - 未 submit_plan 时 gate 不拦（plan_gated=True 但未触发）
  - submit_plan 后同轮其它写工具被锁
  - 审计记 `result=deny reason=plan_gate`
- **备注**：

---

## G3. 文档运营工具

### [ ] G3-001 doc_tag 基础操作 — **P1** 🔴 需 LLM

- **前置**：target_doc_id + ≥CONTRIBUTOR
- **步骤**：
  1. 让 sub_archivist 调用 doc_tag，set/append/remove 标签
  2. 查 `Document.meta_fields.tags`
- **预期**：
  - 响应 `{ok, doc_id, tags, next_steps}`
  - 审计 `action=agent_v2.doc_tag result=allow`
- **备注**：

### [ ] G3-002 doc_rename — **P2**

- **前置**：≥CONTRIBUTOR
- **步骤**：
  1. 调 doc_rename，`{doc_id, new_name}`
- **预期**：
  - `Document.name` = 新名字
  - 禁用控制字符 / Windows 保留字；同 KB 重名自动加后缀
- **备注**：

### [ ] G3-003 doc_archive（跨 KB 移动） — **P1** 🟡 需 Task Executor

- **前置**：source_kb + target_kb，caller 在**两边**都 ≥CONTRIBUTOR
- **步骤**：
  1. 调 doc_archive，`{doc_id, target_kb_id}`
  2. 确认源 KB doc 消失，target KB doc 出现
- **预期**：
  - 响应 `{ok, source_doc_id, target_doc_id, next_steps}`
  - ES chunks kb_id 批更新 + 两端计数同步
  - 同 tenant + 同 embedding 硬约束（违反时返 err）
- **备注**：

### [ ] G3-004 doc_reparse — **P1** 🟡 需 Task Executor

- **前置**：≥CONTRIBUTOR
- **步骤**：
  1. 调 doc_reparse，`{doc_id}`
  2. 观察 task_executor 日志
- **预期**：
  - 工具立即返回 `{ok, doc_id, next_steps}`
  - chunks 清 + 新 parse 任务入队
  - `doc.parsing_status` PROCESSING → SUCCESS
- **备注**：

### [ ] G3-005 doc_upload_from_url — **P1** 🟡 需网络

- **前置**：target_kb_id + ≥CONTRIBUTOR
- **步骤**：
  1. 调 `{kb_id, url: "https://...", doc_name}`
  2. 等待 3-5s 完成
- **预期**：
  - SSRF 防护（private/loopback/link-local IP 拒）
  - 50MB 上限 + xxhash128 dedup
  - 新 Document 已解析 + embedding 生成
- **备注**：

### [ ] G3-006 kb_create — **P2**

- **前置**：≥VIEWER（任何人可建）
- **步骤**：
  1. 调 kb_create，`{kb_name, description}`
  2. 验证当前用户成 OWNER
- **预期**：
  - `Knowledgebase` 新增 + `DatasetAccess` 新增 (user, kb, OWNER)
  - embedding 继承 session 第一个 KB
  - kb_max 配额硬拒
- **备注**：

---

## G4. 自省工具 + sub_librarian

### [ ] G4-001 kb_audit — **P2**

- **前置**：target_kb_id + ≥VIEWER
- **步骤**：
  1. sub_librarian 调 kb_audit
- **预期**：
  - 响应含陈旧/重复/未解析/top tags
  - 耗时 ~600ms，无 side effects
- **备注**：

### [ ] G4-002 kb_stats — **P2**

- **前置**：≥VIEWER
- **步骤**：
  1. 调 kb_stats
- **预期**：
  - <1KB 快照（doc_count / chunk_count / embedding_coverage_pct）
  - 耗时 ~90ms
- **备注**：

### [ ] G4-003 doc_create_note — **P2**

- **前置**：≥CONTRIBUTOR
- **步骤**：
  1. sub_librarian 调 doc_create_note，`{kb_id, title, content: markdown}`
- **预期**：
  - 新 Document 创建 + chunks 生成
  - `plan_gated=False`（唯一跳过 plan gate 的写工具）
  - (kb_id, title) 去重
- **备注**：

### [ ] G4-004 doc_list_recent_changes — **P2**

- **前置**：≥VIEWER，KB 有最近变更
- **步骤**：
  1. 调 doc_list_recent_changes，`{kb_id, days: 7}`
- **预期**：
  - 按 updated_at 倒序，最多 50 条
  - change_type = created/updated/tagged/archived
- **备注**：

---

## G5. 交互工具 + Plan Gate 执行循环

### [ ] G5-001 ask_user_question 基础 — **P1** 🔴 需 LLM

- **前置**：supervisor 需要澄清的场景
- **步骤**：
  1. 让 supervisor 触发 ask_user_question
  2. 前端应弹"待问题卡片"
  3. 用户选选项或输入答案
  4. 下一轮 supervisor 能读到答案
- **预期**：
  - SSE 有 `type=ask_user_question` 事件
  - 审计 `action=agent_v2.ask_user_question`
- **备注**：

### [ ] G5-002 submit_plan 全流程审批 — **P0** 🔴 需 LLM

- **前置**：sub_archivist 执行 ≥3 操作的场景
- **步骤**：
  1. sub_archivist 调 submit_plan（title/steps/affected_resources/risk_level/reversible）
  2. 前端弹"待审批计划卡片"
  3. 用户点"批准"
  4. 下一条 user message 自动带 `[plan approved]` 前缀
  5. 后端 `parse_plan_decision` 识别 + `transition_plan_status` → 'approved'
  6. sub_archivist 恢复，调 get_pending_plan，逐步执行
- **预期**：
  - `AgentV2Session.pending_plan_id/status/submitted_at` 被填
  - SSE 有 `plan_submitted` 事件含完整 plan
  - 审计 `action=agent_v2.plan_submit` + metadata
  - 批准后：装饰器 `@require_kb_write` 检查 `pending_plan_status='approved'` 后解锁
  - 拒绝后：status='rejected'，gate 继续锁
  - 修改后：status='request_changes'，sub_archivist 重新规划
- **备注**：

### [ ] G5-003 Plan Gate 跨轮状态保持 — **P1** 🔴 需 LLM

- **前置**：上 turn 已 submit_plan 且 status='waiting'
- **步骤**：
  1. 下 turn 用户发无前缀消息
  2. 再下 turn 试写工具
  3. 最后 turn 发 `[plan approved]`
- **预期**：
  - 未决策时 pending_plan_status 跨 turn 保持
  - 写工具被 gate 拦截（DB live read）
  - 1 小时 TTL 后自动清
- **备注**：

### [ ] G5-004 [plan request changes] 流程 — **P2** 🔴 需 LLM

- **前置**：有待审批 plan
- **步骤**：
  1. 发 `[plan request changes] 只处理 2024 年以后的文件`
  2. 后端识别 + 保存注记
  3. sub_archivist 基于反馈重新规划
- **预期**：
  - status='request_changes'，注记保存
  - 新 plan 的 pending_id 与旧不同
  - 两轮 plan 都有审计
- **备注**：

---

## G6. Citation Validator

### [ ] G6-001 warn 模式（默认） — **P2** 🔴 需 LLM

- **前置**：`session.citation_enforce_level='warn'`
- **步骤**：
  1. 让 Agent 答复中含无脚注的数字断言
- **预期**：
  - SSE 有 `citation_warning` 事件 level=warn
  - 答复不被改写
  - 前端显示 ⚠️
- **备注**：

### [ ] G6-002 strict 模式 + rewrite — **P2** 🔴 需 LLM

- **前置**：`citation_enforce_level='strict'`
- **步骤**：
  1. Agent 同上答复
  2. `rewrite.py` 一次性重写
- **预期**：
  - 答复被改写（补脚注或改表述）
  - `citation_warning` level=`strict_rewritten`
  - 前端展示改写后的版本 + banner
- **备注**：

### [ ] G6-003 strict_numeric 模式 — **P2** 🔴 需 LLM

- **前置**：`citation_numeric_strict=true`
- **步骤**：
  1. 答复含 evidence 里不存在的数字
- **预期**：
  - 触发 `number_unsupported` 规则
  - strict 模式下改写为"不确定"或删除
- **备注**：

---

## G7. 多轮上下文 + Compactor

### [ ] G7-001 历史消息拉取与合成 — **P1** 🔴 需 LLM

- **前置**：session 已有 ≥10 条消息
- **步骤**：
  1. 发新消息
  2. 观察 `_build_prompt_with_history()` 是否生效
- **预期**：
  - prompt 开头含 `<conversation-history>` block
  - LLM 能回答"之前我们说过..."
- **备注**：

### [ ] G7-002 Compactor 触发（≥20 条） — **P1** 🔴 需 LLM

- **前置**：session 已有 20+ 条消息
- **步骤**：
  1. 发第 21 条
  2. 后台 `run_compact_safely` 触发
- **预期**：
  - `session.summary_text` 被填
  - `summary_until_seq=20`
  - 审计 `action=agent_v2.compact result=allow`
  - 后续 turn 跳过前 20 条，用 summary
- **备注**：

### [ ] G7-003 Compactor 异常处理 — **P2**

- **前置**：故意让 compact 失败（关 LLM API）
- **步骤**：
  1. 触发 compact
  2. 查审计日志
- **预期**：
  - 异常不影响 SSE 主流
  - 审计 `action=agent_v2.compact result=deny reason=异常类名`
  - 后续 turn 继续工作（无 summary）
- **备注**：

---

## G8. RBAC + 审计

### [ ] G8-001 四角色权限检查 — **P1** 🟡 需多账号

- **前置**：同 tenant 下 3 个账号 + 1 个 KB
- **步骤**：
  1. OWNER grant CONTRIBUTOR 给 user2
  2. CONTRIBUTOR 可创建 session + 调 doc_tag（通过）
  3. VIEWER 调 doc_tag → 应拒（no_access）
- **预期**：
  - 角色分级生效
  - 无权时审计 `result=deny reason=no_access`
- **备注**：

### [ ] G8-002 跨租户隔离 — **P0** 🟡 需多 tenant 🔒 安全关键

- **前置**：2 tenant 账号
- **步骤**：
  1. tenant_A 创建 session + kb_A
  2. tenant_B 尝试访问 tenant_A 的 session → 404
  3. tenant_B 在自己 session 中 spawn_subagent 用 kb_A → 拒
- **预期**：
  - session 跨 tenant 不可见
  - 审计 `result=deny reason=cross_tenant_denied`
- **备注**：

### [ ] G8-003 隐式 VIEWER 权限（kb.permission='team'） — **P1**

- **前置**：KB permission='team'，同 tenant 另一 user 未被显式 grant
- **步骤**：
  1. user_B（同 tenant）创建 session + 该 KB → 应通过（隐式 VIEWER）
  2. user_B 调 doc_tag → 应拒（权限不足）
  3. user_C（不同 tenant）访问 → 拒
- **预期**：
  - 隐式 VIEWER 生效
  - 写操作仍被拒
- **备注**：

### [ ] G8-004 审计日志 UI 查询 — **P1**

- **前置**：已有多条审计日志
- **步骤**：
  1. 前端 `/user-setting/audit-log`
  2. 按 action / resource_type / result / 时间范围过滤
  3. 点记录展开 metadata
- **预期**：
  - 分页 + 过滤生效
  - 敏感字段（密钥）不暴露
- **备注**：

---

## G9. 飞书机器人渠道

### [ ] G9-001 Bot Channel 创建与 Webhook 配置 — **P1** 🟡 需飞书凭证

- **前置**：飞书 app_id/app_secret/verification_token/encrypt_key
- **步骤**：
  1. 前端 `/user-setting/bot-channels` 点新增
  2. 填凭证 + 保存
  3. 复制 webhook_url 回飞书后台配置
  4. 飞书 URL verification
- **预期**：
  - secret 字段级加密（DB 存 `enc:v1:<base64>`）
  - URL challenge 返回 challenge 值
  - status='active'
- **备注**：

### [ ] G9-002 飞书消息接收与签名校验 — **P1** 🟡 需飞书真环境

- **前置**：Bot Channel 已配
- **步骤**：
  1. 飞书群 @机器人 发问题
  2. 观察后端日志
- **预期**：
  - 签名校验通过（基于 encrypt_key）
  - 消息解析成 InboundMessage
  - 2s 内响应飞书
- **备注**：

### [ ] G9-003 Agent 回复通过飞书发送 — **P1** 🔴 需 LLM

- **前置**：Agent 已执行完
- **步骤**：
  1. 观察飞书群消息
- **预期**：
  - 群里看到机器人回复
  - 脚注格式清晰
  - 超长消息自动截断
- **备注**：

### [ ] G9-004 消息去重 — **P2**

- **前置**：网络重试场景（可手工 curl 同 event_id 2 次）
- **步骤**：
  1. 同 event_id 投两次
- **预期**：
  - 用户只看到一条回复
  - Redis dedup 优先，内存兜底
  - dedup key 摘要化（非明文）
- **备注**：

### [ ] G9-005 会话范围（user/group） — **P2**

- **前置**：同 user 在 2 个群
- **步骤**：
  1. 在 group_A 问问题
  2. 在 group_B 问问题
  3. 查 `BotConversationMap`
- **预期**：
  - 默认 session_scope='group_sender'，两个群不同 session
  - 可配置为 'sender' 共享
- **备注**：

---

## G10. 租户配额 + 用量 + 限流

### [ ] G10-001 查询配额与今日用量 — **P1**

- **前置**：已有若干 Agent calls
- **步骤**：
  1. `GET /v1/tenant_quota`
  2. 前端 `/user-setting/usage`
- **预期**：
  - quota + today + month + KB/Doc 实时计数
  - 数字与实际消耗一致
- **备注**：

### [ ] G10-002 用量日曲线（30 天） — **P2**

- **步骤**：
  1. `GET /v1/tenant_quota/range?days=30`
  2. 前端看柱图
- **预期**：
  - 按日期升序，缺失日期填 0
  - 柱图正常渲染
- **备注**：

### [ ] G10-003 配额超额检查（token） — **P2**

- **前置**：`monthly_token_budget=1000`，已用 950
- **步骤**：
  1. 发一个消耗 100+ tokens 的 call
- **预期**：
  - `hard_enforce=1` 时直接 429/403
  - `hard_enforce=0` 只记审计不阻塞
- **备注**：

### [ ] G10-004 文档数配额 — **P2**

- **前置**：`monthly_doc_count_limit` 接近上限
- **步骤**：
  1. sub_archivist 尝试 doc_upload_from_url
- **预期**：
  - 超额时 err + 审计 `reason=quota_exceeded`
- **备注**：

### [ ] G10-005 API Token 速率限制 — **P2** 🟡 需 Redis

- **前置**：APIToken rps=5
- **步骤**：
  1. 快速发 10 个请求
- **预期**：
  - 前 5 通过，第 6-10 返 429
  - Redis `lua_token_bucket` 优先；不可用回退内存
  - key SHA-256 摘要化（token 明文不落 Redis）
- **备注**：

---

## G11. 前端产品化

### [ ] G11-001 登录页 — **P1**

- **步骤**：访问 `/login-next`
- **预期**：
  - 知源品牌 logo + 产品名
  - 表单验证 + 登录成功跳首页
- **备注**：

### [ ] G11-002 首页 + 导航 — **P1**

- **步骤**：访问 `/home`
- **预期**：
  - Sidebar 折叠/展开有记忆
  - 活跃 nav 高亮
  - 快速开始 + 最近会话 + 知识库卡片
- **备注**：

### [ ] G11-003 Agent Chat 主页面 — **P1** 🔴 需 LLM

- **步骤**：`/agent-chat`，新建对话 + 发消息
- **预期**：
  - 左侧 session 列表可滚动/搜索/删
  - SSE 流式显示（token by token）
  - Shift+Enter 换行 / Enter 发送
- **备注**：

### [ ] G11-004 待问题卡片（ask_user_question） — **P1** 🔴 需 LLM

- **步骤**：触发 ask_user_question
- **预期**：
  - 卡片独立于消息气泡
  - 支持多选/单选 + 自由文本兜底
  - 答案转为新 user message
- **备注**：

### [ ] G11-005 待审批计划卡片（submit_plan） — **P0** 🔴 需 LLM

- **步骤**：触发 submit_plan
- **预期**：
  - 卡片展示：标题 / 步骤 / 影响资源 / 风险色 chip（绿/琥珀/红）/ 可逆性
  - 三按钮：批准 / 拒绝 / 修改
  - 修改时弹输入框
  - 用户决策后新 message 自动带前缀
- **备注**：

### [ ] G11-006 Citation Warning 显示 — **P2** 🔴 需 LLM

- **步骤**：触发 citation_warning
- **预期**：
  - 警告标记 ⚠️ 清晰
  - hover/点击显示具体问题
  - strict_rewritten 答复展示改写版本
- **备注**：

### [ ] G11-007 数据集成员权限管理（RBAC UI） — **P1**

- **步骤**：`/dataset/{id}/members`
- **预期**：
  - 成员列表 + role 下拉 + 删除按钮
  - 邀请弹窗（多 email 支持）
  - 无权操作被禁用
- **备注**：

### [ ] G11-008 用户设置 - 审计日志页 — **P1**

- **步骤**：`/user-setting/audit-log`
- **预期**：
  - 表格分页（每页 50 条）
  - 过滤联动
  - 时间快捷（今天/本周/本月/自定义）
  - metadata JSON 格式化展开
- **备注**：

### [ ] G11-009 用户设置 - 配额与用量页 — **P2**

- **步骤**：`/user-setting/usage`
- **预期**：
  - 4 张 stat card（KB / Doc / Tokens / Cost）+ 进度条
  - 今日用量 chips
  - 30 天 Token 柱图（纯 CSS）
  - `hard_enforce=1` 时徽标提示
- **备注**：

### [ ] G11-010 用户设置 - 飞书机器人配置页 — **P1**

- **步骤**：`/user-setting/bot-channels`
- **预期**：
  - 渠道列表 + status
  - 新增弹窗表单验证
  - webhook_url 自动生成 + 复制 toast
  - 脱敏显示（secret 显示 `***`）
  - 更新时空值/`***` 不覆盖原 secret
- **备注**：

### [ ] G11-011 数据集 - 检索测试 Lab — **P2**

- **步骤**：KB 详情 → "检索测试" tab
- **预期**：
  - 输入 query → top-5 结果 + 高亮 + 相关度分数
  - chunk 可复制
- **备注**：

### [ ] G11-012 知识图谱可视化 — **P2**

- **前置**：KB 已建图
- **步骤**：KB 详情 → "图谱" tab
- **预期**：
  - 图谱 <3s 渲染
  - 支持拖动/缩放/点击节点
- **备注**：

---

## G12. 上游回归

### [ ] G12-001 上游 Dialog 画布模式 — **P1**

- **步骤**：访问 `/agent` 旧画布编辑器
- **预期**：
  - 画布无报错，节点连接 OK
  - 保存 + 测试执行成功
  - **fork 未破坏上游**
- **备注**：

### [ ] G12-002 文档解析流水线（deepdoc） — **P1** 🟡 需 Task Executor

- **步骤**：上传 PDF/Excel/DOC
- **预期**：
  - OCR + layout analysis 正常
  - chunks 生成数合理
  - 无 fork regression
- **备注**：

### [ ] G12-003 GraphRAG 构建 — **P2**

- **步骤**：上传有结构化信息的文档 + `rag_graph_query`
- **预期**：
  - 节点/关系正常生成
  - 图查询返回结果
- **备注**：

### [ ] G12-004 原版 Agent 画布工具调用 — **P2**

- **步骤**：原版 workflow 用 Tavily / ExeSQL
- **预期**：
  - 工具调用正常
  - 无 fork 黑名单干扰
- **备注**：

---

## 已被自动化覆盖可 skip 的

见 `test/agent_v2/`（229 passed / 8 skipped），以下纯逻辑**人工可 skip**：

| 测试文件 | 覆盖范围 |
|---|---|
| `test_annotations.py` | 18 工具元数据 parity + next_steps 裁剪 |
| `test_compactor.py` | 历史摘要算法 + run_compact_safely 五场景 |
| `test_plan_execution_loop.py` | plan gate + decision parse |
| `test_validators.py` | citation validator 三规则 + rewrite 兜底 |
| `test_doc_ops_*.py` | 6 写工具 + 4 自省工具 CRUD |
| `test_definitions.py` | 10 AgentDefinition 注册 |
| `test_registry.py` | 18 工具注册 parity |

**必须人工验**（自动化覆盖不了）：
- SSE 流渲染 + 前端卡片交互（G11）
- 飞书网络 + 时序（G9）
- 多 tenant 多 user 交叉场景（G8）
- submit_plan 完整多轮审批循环（G5-002）

---

## 规模统计

| 分组 | 用例数 | P0 | P1 | P2 | 🔴 需 LLM | 预计耗时 |
|---|---:|---:|---:|---:|:---:|---:|
| G1 | 3 | 3 | 0 | 0 | ❌ | 10 min |
| G2 | 5 | 0 | 4 | 1 | ✓ | 30 min |
| G3 | 6 | 0 | 3 | 3 | ✓ | 30 min |
| G4 | 4 | 0 | 0 | 4 | ❌ | 20 min |
| G5 | 4 | 1 | 2 | 1 | ✓ | 40 min |
| G6 | 3 | 0 | 0 | 3 | ✓ | 20 min |
| G7 | 3 | 0 | 2 | 1 | ✓ | 20 min |
| G8 | 4 | 1 | 3 | 0 | ❌ | 30 min |
| G9 | 5 | 0 | 3 | 2 | 部分 | 45 min |
| G10 | 5 | 0 | 1 | 4 | ❌ | 30 min |
| G11 | 12 | 1 | 6 | 5 | 部分 | 60 min |
| G12 | 4 | 0 | 2 | 2 | ❌ | 20 min |
| **合计** | **58** | **6** | **26** | **26** | ~18 | **~5.5 h** |

**图例**：🔴 需 live LLM  |  🟡 需特殊环境（Redis/MySQL/多账号/网络/飞书）  |  🔒 安全关键

---

## 验收标准

- **必过**：所有 P0（6 条）+ 所有 P1 中带 🔒 或标 "核心差异化" 的用例
- **推荐过**：P1 全部通过，P2 ≥70% 通过
- **任何失败**：写清重现步骤 + 影响范围，不要静默跳过

失败反馈写在对应用例的「备注」行，测试结束后汇总到 `STATUS.md` 的"下一步入口"。
