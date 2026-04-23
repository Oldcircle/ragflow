# RAGFlow Agent 二改 — 进度快照

> **STATUS.md 是会话交接文档**，每次有实质进展时更新，只记录"当前状态 + 下一步入口"，不是历史日志。

---

## 最近更新：2026-04-23（Phase 2.5 审计差距修补 + 测试覆盖完成）

**当前阶段**：**Phase 2 全部 + Phase 3.1 + Phase 3.2 + Phase 2.5 完整实现 + P2.5-hardening 完成**
**下一步入口**：
1. （前端 nice-to-have）`NewSessionDialog` 从 `/v1/agent_v2/definition` 拉模板，替代硬编码的 6 个 `/template`；设置抽屉里暴露 `history_turn_limit` 和 `citation_enforce_level` 选项
2. （P2.5.1 follow-up）保障房 10 题重跑验证 validator（warn 模式）命中/空报的平衡
3. （P2.5.2 follow-up）多轮上下文真机追问用例（要登录态 curl / Playwright，不能硬写单测）
4. 之后：P3.3 版本管理 / PII / 企业管理台，或 P3.2c 钉钉/企微 adapter（用户说延后）

### P2.5-hardening 完成内容（2026-04-23，claim vs reality 差距修补）

外部审计指出 Phase 2.5 三个 commit（`540bfb91f` / `86fb8e867` / `c921ea729`）有
"文档/Literal 承诺了但代码没实现"的三处差距。本批次全部闭合：

**P2.5.1 citation validator 补齐（本批）**：
- `api/agent_v2/validators/citation.py` — 实现缺失的 **rule 3 `no_citation_for_numeric`**：
  句子切分（中英混排，`。！？!?\n` 为边界）→ 对每个数字型断言定位所在句 →
  检查 ±2 句窗口内是否存在 `[N]` 脚注；无则触发。rule 2 先发时 rule 3 跳过避免双报
- 顺手修 pre-existing bug：`_CITE_RE` 的 `(?<!\])` lookbehind 把 `[1][2][3]` 连排的
  第 2、3 个都拦掉了（违背注释自己说的"允许连排"），去掉 lookbehind，留 `(?!\()`
  足够防住 markdown link
- `api/agent_v2/validators/rewrite.py`（新）— 实现 **strict 模式 one-shot rewrite**：
  HTTP 直连 `/v1/messages`（兼容 Anthropic + DeepSeek-anthropic），复用 compactor
  的思路；auth 空 / evidence 空 / HTTP≠200 / 异常 都返 None 让上层降级
- `api/agent_v2/runner.py::_handle_citation_issues`（新）— 把 warn / strict 分叉：
  - warn：直接 emit `citation_warning` level=warn
  - strict：调 rewrite → 复检通过则 emit `text_delta`（追加「校正答复」banner） +
    `citation_warning` level=**strict_rewritten**；失败则 emit fallback text +
    level=strict_failed。这样 Literal 里的 `strict_rewritten` 第一次被真正 emit 过

**P2.5.2 compactor 可观测性补齐（本批）**：
- `api/agent_v2/compactor.py::run_compact_safely`（新）— 异常安全入口。任何
  `maybe_compact_session` 内部异常都 **同步** 写 `access_audit_log`（action=
  `agent_v2.compact`，result=deny，reason=异常类名+截断 msg）；成功 / skip 也
  写 allow 记录，让运维在审计日志 UI 可见 compactor 健康
- `api/apps/agent_v2_app.py` — fire-and-forget 换用新入口，删掉原来 `contextlib.
  suppress(Exception)` 包 `create_task` 的"假保护"（它只防 create_task 自身，
  不防 task body）；现在 task body 的异常由 run_compact_safely 吃 + 审计

**新 pytest 覆盖（上架前硬缺）**：
- `test/agent_v2/test_validators.py` — 32 用例：extract_numbers 六大 kind 边界 +
  EvidenceIndex 往返（JSON / MCP envelope / read_doc pages / chunk 去重）+
  validate_citations 三条规则（含 rule 2 > rule 3 优先级、markdown link 不误伤、
  numeric_strict=False 短路）+ rewrite_answer_strict 六条兜底路径（空 auth /
  空 issues / 空 evidence / 成功 / 非 200 / 异常）
- `test/agent_v2/test_compactor.py` — 23 用例：should_compact / split_for_compact
  边界 + summarize_history httpx mock（包含 previous_summary 拼接）+
  maybe_compact_session 五场景（missing session / 阈值未到 / 摘要空 / 正常写 /
  prev_until 跳已压范围）+ **run_compact_safely 四场景**（异常→deny 审计 /
  成功→allow summary_written / skip→allow skipped / 审计自己挂了不破坏主流程）
- `test/agent_v2/test_definitions.py` — 17 用例：registry auto-load / 无 name
  冲突 / kind 过滤 / to_dict 可 JSON 序列化 / resolve_tools 六种组合（"*" +
  parent / "*" + None / explicit / 空 / disallowed_tools 从 "*" 扣 / 从 explicit
  扣）+ callable system_prompt + ModelRef vs "inherit" to_dict
- `test/agent_v2/test_rbac_cross_tenant.py` — **跨租户穿透**：3 unit（rag_retrieve
  深度防御在 kb 全拒 / 部分拒 / 匿名 user 三场景）+ 5 真 DB 集成用例（
  RAGFLOW_TEST_DB=1 gate）：owner 角色兜底、跨 tenant 完全 deny 且 audit、
  匿名 user 无访问、显式 grant 覆盖兜底、禁止 grant OWNER
- `test/agent_v2/conftest.py`（新）— 预热 xgboost，绕 pyproject filterwarnings=
  ["error"] 把 pkg_resources UserWarning 升级成 import 错误的坑
- `test/agent_v2/test_registry.py` — 更新 expected 集合把 P2.3 的 spawn_subagent
  纳入（老测试一直 stale）

**Lint 债收口**：ruff 在 `api/agent_v2/` 和 `test/agent_v2/` 上全绿，顺手清掉
`schema.py` 未用的 `field`、`runner.py` 未用的 `pending_tool_events`、
`trigger_worker.py` 未用的 `time`

**验证**：
- `pytest test/agent_v2/` → **112 passed, 8 skipped**（8 个是 RAGFLOW_TEST_DB=1
  gate 的跨租户 DB 集成）
- `RAGFLOW_TEST_DB=1 pytest test/agent_v2/` → **120 passed**（含 5 个跨租户
  DB 集成全过，验证 effective_role / has_at_least / filter_accessible / grant /
  revoke + OWNER 不能显式授）
- `uvx ruff check api/agent_v2/ test/agent_v2/` → All checks passed!

**审计评分变化**：之前评审给 ToB 5-6/10。本批修完 3 个 claim/reality 差距
+ 120 自动化 pytest 覆盖 + 跨租户渗透有 proof，**评分应上抬到 7/10**——
可以对内部客户灰度；要到 8+ 还得等 Phase 3 的任务生命周期 + Langfuse 深度集成。

### Phase 2.5 完成内容（2026-04-23）

**P2.5.1 Citation Validator**（commit `540bfb91f`）：
- `api/agent_v2/validators/{evidence_index,citation}.py` — Evidence 索引（按 [N] 编号存 chunk + 从内容抽百分比/年限/金额/年龄/日期/裸数字 `NumberMatch`），`validate_citations()` 同时检测 `missing_chunk`（[N] 越界）与 `number_unsupported`（答复里的数字没在任何 evidence 里出现）
- `api/agent_v2/runner.py` — 流式期间 `_collect_evidence_from_tool()` 拦截 rag_retrieve / rag_read_doc / rag_graph_query 结果喂给 EvidenceIndex；在 `end` 事件前跑 `validate_citations`，报错时发 `citation_warning` 事件
- `api/agent_v2/event.py` — 新事件类型 `citation_warning` + 工厂；`CitationWarning.level = warn | strict_rewritten | strict_failed`
- `api/db/db_models.py` + `services/agent_v2_service.py` — `agent_v2_session` 加 nullable 列 `citation_enforce_level`（off/warn/strict）+ `citation_numeric_strict`（1/0）；ALTER TABLE 已对存量表应用
- `api/apps/agent_v2_app.py` — create_session / conversation 两端把新字段接通到 AgentRunner
- 前端 `web/src/pages/agent-chat/hooks/use-agent-stream.ts` + `components/message-list.tsx` — 新 `CitationWarning` 类型 + `CitationWarningPanel` 组件（按 severity 着色，`strict_failed` 红色、`warn` 黄色）；i18n keys `agentV2.citation*` 双语齐全
- 验证：5/5 smoke 用例过（number 抽取 / EvidenceIndex 往返 / missing_chunk / number_unsupported / compound 幻觉）

**P2.5.2 多轮上下文 + Compact Summary**（commit `86fb8e867`）：
- `api/agent_v2/runner.py` — `run()` 新参数 `history=...` + `summary_text=...`；`_build_prompt_with_history()` 把历史拼成 `<conversation-history>` + `<current-user-message>` 两段块（Claude Agent SDK `query()` 只吃 str，所以走"合成 user message"路径 B，参考 `claude-code-ref/forkSubagent.ts::FORK_BOILERPLATE_TAG`）
- `api/agent_v2/compactor.py` — 新模块：`should_compact`（阈值 20 条消息）、`split_for_compact`（保留最近 10 条，其余并入 summary）、`summarize_history`（直接 httpx POST /v1/messages，兼容 Anthropic 和 DeepSeek-anthropic）、`maybe_compact_session`（fire-and-forget 总控）
- `api/apps/agent_v2_app.py::conversation` — 拉 session history → 过滤本轮 user msg → 限定到 `summary_until_seq` 之后 → 传给 runner；收流后 `asyncio.create_task(maybe_compact_session(...))`，不阻塞 SSE teardown
- `api/db/db_models.py` + `services/agent_v2_service.py` — 3 个新 nullable 列：`history_turn_limit`（默认 10）、`summary_text`（TEXT）、`summary_until_seq`（INT）；`list_for_runner`（asc 排序 + role/exclude/since_create_time 过滤）+ `save_summary`
- 验证：7/7 integration smoke 过（seed 22 轮 → list_for_runner 正确返回 → exclude_message_id 生效 → compact 触发 → summary 持久化 → since_create_time 下一轮正确跳过旧消息 → 幂等不重复 compact）

**P2.5.3 Agent Definition Manifest**（commit `c921ea729`）：
- `api/agent_v2/definitions/schema.py` — `AgentDefinition` 数据类（name / version / kind supervisor|subagent / system_prompt（支持 callable）/ model `ModelRef|"inherit"` / tools `list|"*"` / disallowed_tools / kb_scope / citation_enforce / can_spawn_subagents / allowed_subagent_types / history_turn_limit），`resolve_tools()` 按父工具集展开 `"*"`
- `api/agent_v2/definitions/registry.py` — pkgutil 扫 `built_in/` 自动注册；`get_definition(name)` / `list_definitions(kind=...)`，DB 自定义留到 Phase 3
- `api/agent_v2/definitions/built_in/` — 6 个 supervisor（baozhang / generic-policy / legal-contract / research-analyst / customer-support / internal-wiki）+ 2 个 subagent（sub_policy_researcher / sub_evidence_checker）；供 `claude-code-ref/built-in/*.ts` 的一文件一 Agent 风格
- `api/agent_v2/tools/spawn_subagent.py` — 新 `subagent_type` 参数：找 definition → 查 `ctx.allowed_subagent_types` 白名单 → 用 definition 的 tools / max_turns / max_budget_usd / system_prompt / citation_enforce 组装子 runner；4 个错误分支（unknown / wrong_kind / not_allowed / tools_unavailable）
- `api/agent_v2/tools/base.py::ToolContext` — `allowed_subagent_types: tuple[str, ...] | None`（None = 不做限制，老 session 无感升级）
- `api/apps/agent_v2_app.py` — 新端点 `GET /v1/agent_v2/definition?kind=supervisor|subagent`
- 验证：registry 8 个 definition 全部加载无冲突；spawn_subagent 4/4 gate 用例过（unknown / wrong_kind / not_allowed / 正常路径：child 拿到 tools=[rag_retrieve,rag_read_doc]/max_turns=6/enforce=warn）

**设计文档**：`PLAN-agent-runtime-maturity.md`（2026-04-23 新增）

### Phase 2.5 背景（2026-04-23 外评）

外评指出 Agent v2 三个结构性缺口：
1. **答案可信度没有硬保障**：`[N]` 脚注和数值型断言未做后置校验，LLM 可以编数字随手标脚注
2. **session 在 UI 上有、模型侧无状态**：`agent_v2_app.py:371` 每轮新开 runner 只传当前 user_message，`runner.py:180` 进 SDK 的是 `query(prompt=user_message)` 没历史；追问必崩
3. **Agent / Tool 定义硬编码**：`registry.py` + DB session 列 + 模板文件三处分散，加不了命名 subagent

我们**不完全同意外评的优先级**（外评把多轮上下文排第一），判断：对企业 KB 产品**可信度 > 可用性 > 可扩展性**，因此优先级是 Validator → 多轮 → Definition。

**全程对标 `~/Opensource/vendor/claude-code-ref/`**：`AgentTool/loadAgentsDir.ts`、`runAgent.ts`、`forkSubagent.ts`、`resumeAgent.ts`、`built-in/*.ts`。只抄模式不抄代码（Bun/TS 代码 Agent 场景和 Python KB Agent 场景不能直搬）。每个模块的"抄什么 / 不抄什么"在 `PLAN-agent-runtime-maturity.md` 第三节有精确映射表。

**明确不做（延 Phase 3）**：任务生命周期从 HTTP 解耦（agent_task + worker queue）、完整 transcript replay、工具 policy hook、permission mode、fork agent、prompt cache 优化。理由详见设计文档第十节。

### P3.1 完成内容（2026-04-23）

企业真正要卖出去的三件合规 + 运维基线硬需求：

**P3.1a 审计日志 UI**（commit `345e66c66`）：
- 新页面 `/user-setting/audit-log`，把 P2.1 已经在写的 `access_audit_log` 表可视化给管理员
- 筛选：action / resource_type / resource_id / result（allow|deny）/ user_id / 时间范围 / 分页
- 每条可展开看 reason / user-agent / metadata JSON；allow = 绿盾；deny = 红警示
- 后端 `/v1/audit_log/list` 端点 P2.1 已做，本批只补前端

**P3.1b 租户配额 + 用量计量**（commit `a9b1a999e`）：
- 两张新表：`tenant_quota`（kb/doc/token-月/api-rps/bot-日/subagent-日 + hard_enforce 开关）+ `tenant_usage_daily`（按日 rollup 6 个指标）
- `TenantQuotaService` / `TenantUsageService`：默认值兜底（不落库），原子 UPSERT 增量；`QuotaExceeded` + 三个 `check_*` helper
- 三处打桩：Agent v2 `conversation` 结束时记 token / cost / subagent；bot webhook receive 记 bot_messages；`spawn_subagent` 工具记 subagent_spawns
- 新端点 `GET /v1/tenant_quota`（当前限额 + 今日 + 本月 + KB/Doc 实时计数）+ `GET /v1/tenant_quota/range?days=30`
- 新页面 `/user-setting/usage`：4 张带进度条的 stat card（KB / Doc / Tokens / Cost）+ 今日用量 chips + 30 天 Token 柱图（纯 CSS 自画，无第三方图表库）+ 硬执行模式徽标
- `hard_enforce=0`（默认）只记审计不阻塞；`=1` 才直接 429/403

**P3.1c APIToken 限流**（本批）：
- 新模块 `api/utils/rate_limit.py` — 进程内 token bucket，`try_take / check_rate / RateLimited`
- 插入 `token_required` 装饰器：命中 APIToken 后按 `tenant_quota.api_rps_max` 限流；超限写审计 `api.request deny reason=rate_limited` 并返 103；成功累加 `tenant_usage_daily.api_requests`
- 所有限流/计量异常静默吞掉，不得阻塞正常业务

### 验证
- 7 新 DB 表全部自动迁移就位：`dataset_access`、`access_audit_log`、`bot_channel`、`bot_conversation_map`、`agent_v2_subagent_trace`、`tenant_quota`、`tenant_usage_daily`
- 端点：`/v1/tenant_quota`、`/v1/audit_log/list`、`/v1/kb/*/member`、`/v1/bot_channel/*` 全部 401（已注册）；`/v1/bot/_supported` 200；`/v1/bot/<ch>/<acc>/events` 带签名 200 不带 109
- 前端：`/user-setting/{usage,audit-log,bot-channels}` 3 个新管理页 + `/dataset/dataset-member/:id` 成员页 + 全部核心路由 200
- 本地冒烟：quota service 默认值 + increment + range；rate limiter 5 rps 放 5 拒绝第 6、rps=0 无限；subagent guards（depth/count/empty）；RBAC 服务端完整 roundtrip



### P2.3 Multi-Agent 完成（2026-04-23）

**新表**：`agent_v2_subagent_trace(id, parent_session_id, parent_tool_call_id, description, prompt, allowed_tools, max_turns, max_budget_usd, status, result_preview, error, token_usage_json, cost_usd, duration_ms, start_time, end_time)` — 自动迁移已生效。

**核心代码**：
- `api/agent_v2/tools/spawn_subagent.py` — `@tool` 装饰的工具。guard 链：深度 ≤1、每 turn ≤3 个、空 prompt 拒绝、工具白名单必须 ⊆ 父（且不含 `spawn_subagent`）、结果截断 32 KB；finish 后把 `result_preview` 前 4 KB + `token_usage` + `duration` 写回 trace；通过 `event_emitter` 把 `subagent_start / subagent_end` 推到父 SSE 流（前端能实时看到子任务进度）
- `api/db/services/subagent_trace_service.py` — `start / finish / get_by_id / list_by_session`
- `api/agent_v2/tools/base.py::ToolContext` — 扩展字段：`session_id / system_prompt / tool_names / model_config / max_budget_usd / depth / subagent_count_this_turn / event_emitter / current_tool_call_id`；新增 `emit_event` helper
- `api/agent_v2/runner.py`：
  - 构造函数新增 `session_id / parent_session_id / depth`
  - `run()` 创建 `asyncio.Queue` 事件 bus + 后台 task（`_sdk_to_bus`）把 SDK 事件丢进来；`_merge_streams` 单协程 drain bus 再吐给调用方，保证事件顺序（subagent_start ↦ end）
  - `_translate` 在 `ToolUseBlock` 出现时更新 `ctx.current_tool_call_id`，让 `spawn_subagent` 能把 trace 关联到父 tool_use id
- `api/agent_v2/event.py` — 新事件类型 `subagent_start / subagent_end`
- `api/agent_v2/registry.py` — `spawn_subagent` 进 `ALL_TOOLS`
- `api/apps/agent_v2_app.py`:
  - `conversation` 路径把 `session.id` 传给 `AgentRunner`
  - 新端点 `GET /v1/agent_v2/session/<id>/subagent` 列出该 session 下所有子 trace

**前端**：
- `useAgentStream`：识别 `subagent_start / subagent_end` → 自动挂到对应的 `StreamingToolCall.subagent` 子状态
- `ToolCallCard`：检测到 `call.subagent` 时渲染 `SubagentInline`（状态徽标 + 任务描述 + 耗时 + 成本；展开查看 `result_preview` 或错误）；父 tool call 的常规预览此时让位给子视图
- `StreamingToolCall` 类型扩展：新增 `subagent?: SubagentTraceInline` 字段（traceId / description / allowedTools / maxTurns / status / resultPreview / costUsd / durationMs）

**Guard 验证**（三个反面用例全部返回合规错误 JSON）：
- depth ≥ 1 调 `spawn_subagent` → `nested_spawn_forbidden`
- `subagent_count_this_turn >= 3` → `too_many_subagents`
- 空 prompt → `empty_prompt`

**E2E 验证**：
- 5 个工具全注册（含 `spawn_subagent`）
- `ToolContext` 扩展字段跨 contextvars 传递正确
- `SubagentTraceService` start / finish / list 都工作
- HTTP `/v1/agent_v2/session/<id>/subagent` 返 401（未登录→已登录即可用；路由已注册）
- 7 个前端核心路由 200，TS + ESLint 新文件零错误



### P2.2 完成内容（2026-04-23）

**新表（已自动迁移）**：
- `bot_channel` — IM 渠道配置（tenant / channel_type / account_id / config_json / default_kb_ids / session_scope / enabled）
- `bot_conversation_map` — IM 会话↔Agent v2 session 持久映射（同一 conversation_key 跨重启保留 session）

**新 Python 包** `api/bot_channels/`：
- `base.py` — `InboundMessage / OutboundReply / BotChannelAdapter` 协议
- `registry.py` — 按 channel_type 分发
- `dedup.py` — 进程内 LRU + TTL 去重（3 秒内重投保护；生产建议换 Redis）
- `feishu/signature.py` — HMAC-SHA256 验签（`sha256(ts+nonce+encrypt_key+body)`，`hmac.compare_digest` 防 timing attack）
- `feishu/conversation.py` — 会话路由键构造（移植 openclaw `conversation-id.ts`，支持 group / group_sender / group_topic / group_topic_sender 四种 scope）
- `feishu/parser.py` — 事件 JSON → `InboundMessage`（text / post / image 正文提取 + `<at>` 剥离 + `mentions` 识别 + bot self-message 过滤 + DM 自动视为 @bot）
- `feishu/client.py` — `tenant_access_token` 缓存（1.7 小时刷新，过期 code 99991663 自动失效）+ 文本消息发送（`/messages/<id>/reply` 话题保留或 `/messages` 新消息，失败兜底退化）+ bot `open_id` 拉取
- `feishu/adapter.py` — 实现 `BotChannelAdapter`，含长文切片（UTF-8 8KB 每片）+ 引用脚注拼接

**新服务**：
- `BotChannelService` — CRUD（`list_by_tenant / find / get_by_id_for_tenant / create / update / delete`）
- `BotConversationMapService` — 持久映射（`find / upsert / touch / list_by_account / delete`）

**新 HTTP 端点**：
- `POST   /v1/bot/<channel>/<account>/events` — 公网 webhook（**无登录**），完整管道：签名验签 → URL challenge → 去重 → 解析 → 异步 `create_task` 跑 Agent → 发回复
- `POST   /v1/bot/<channel>/<account>/test` — 自检（拉 token + bot open_id）
- `GET    /v1/bot/<channel>/<account>/conversation` — 活跃会话列表
- `DELETE /v1/bot/conversation/<id>` — 归档会话映射
- `GET    /v1/bot/_supported` — 列已注册 adapter（公开）
- `GET/POST/PUT/DELETE /v1/bot_channel/...` — 管理员 CRUD（secret 字段脱敏返回）

**处理管道**（`bot_app._handle_inbound`）：找/建 agent v2 session → 跑 `AgentRunner`（用 `bot_channel.default_*` 配置）→ 抽 rag_retrieve / rag_graph_query 引用 → 通过 adapter.send 把回复 + 脚注发回 IM；失败兜底发歉意消息；所有 receive/reply 写 `access_audit_log`。

**前端**：
- 新路由 `/user-setting/bot-channels`，用户设置侧栏新加「机器人渠道」入口
- 空态卡片 + 列表视图（2 列网格），每卡显示：名称 + 启停徽标 + channel/account + 回调 URL（一键复制）+ App ID + 默认知识库数 + 会话粒度 + 更新时间 + 保存后指引
- 操作按钮：测试连接（调 `/test` 拉 token/open_id）、编辑、删除
- 添加/编辑对话框：渠道类型下拉（钉钉/企微置灰）+ 账号标识 + 名称 + App ID/Secret/Encrypt Key/Verification Token + API Base + 默认 KB 多选 + 会话粒度下拉 + System Prompt textarea + 启用开关
- 编辑态：secret 字段留空表示保留原值（前端不回写脱敏后的假值）
- en + zh i18n 全套（`setting.bot*`，30+ keys）

**E2E 验证**：
- 服务层单测（签名正/反、conversation key 三种 scope、parser 群/私聊/自我过滤、dedup、upsert / find / delete）全通过
- HTTP：缺签名 → 109；带有效签名的 URL challenge → 200 回显；坏签名 → 109；未配置渠道 → 404
- 前端：7 条核心路由 dev server 200，TS/ESLint 新文件零错误
- 2 新 DB 表自动创建成功



### P2.1 完成内容（2026-04-23）

**新表（已自动迁移）**：
- `dataset_access(id, kb_id, user_id, role, granted_by, create/update_time)` — 显式成员角色
- `access_audit_log(id, user_id, tenant_id, action, resource_type, resource_id, result, reason, metadata, ip, user_agent, create/update_time)` — 访问审计

**新服务**（`api/db/services/`）：
- `dataset_access_service.py` — `DatasetRole` 枚举 + `effective_role / has_at_least / require_at_least / filter_accessible_kb_ids / list_members / grant / revoke`
- `audit_log_service.py` — `log / allow / deny / query_logs / count_logs`

**填补的 3 个关键访问控制漏洞**（已验证）：
- `api/db/services/dialog_service.py::async_ask` — kb_ids 批量校验，deny 自动写审计
- `api/apps/agent_v2_app.py::create_session` — 同上 + 成功路径写 allow
- `api/agent_v2/tools/rag_retrieve.py` — 工具执行时深度防御，部分 deny 时只用可访问的子集

**新 HTTP 端点**：
- `GET    /v1/kb/<kb_id>/member` — 列成员
- `POST   /v1/kb/<kb_id>/member` — 添加/更新（VIEWER / CONTRIBUTOR / ADMIN，OWNER 不能显式授）
- `DELETE /v1/kb/<kb_id>/member/<user_id>` — 撤销
- `GET    /v1/audit_log/list?action=&resource_type=&result=...` — 分页查审计
- 全部 `@login_required`；`grant/revoke` 还要求 ADMIN+ 角色

**前端**：
- 新路由：`/dataset/dataset-member/:id` → `web/src/pages/dataset/dataset-members/`
- 知识库侧栏新加「成员」导航项
- 成员页：成员表（头像 + 名 + 邮箱 + 角色徽标 + 角色下拉/移除按钮）+ 「邀请成员」对话框（邮箱 + 角色）
- 隐式 OWNER 行不可改、不可移除；非 ADMIN+ 进入直接显示 access denied 状态
- i18n：`knowledgeList.members*` / `memberRole*` / `memberInvite*` / `memberAccessDenied`（en + zh）

**E2E 验证**：
- DB 端：`grant / promote / revoke / query_members` 全通过；`grant(OWNER)` 正确拒绝
- 服务端：`async_ask` 模拟 owner 通过、nobody 拒绝、空参数 no-op
- HTTP 端：`/v1/kb/<id>/member` 与 `/v1/audit_log/list` 均返 401（路由注册成功）
- 前端：6 条核心路由 dev server 全 200，包括新 `/dataset/dataset-member/:id`，ESLint 干净



### Phase 2 设计文档（2026-04-22）

已基于 4 份平行调研（`vendor/openclaw` 飞书适配、`vendor/claude-code-ref` Agent 架构、RAGFlow 自带 bot/webhook 能力、RAGFlow 现有权限模型）写完 Phase 2 设计，分 4 份文档：

- `PLAN-phase2.md` — 总体路线（三件事：访问控制 / IM 机器人 / Multi-Agent）
- `PLAN-rbac.md` — 数据集 RBAC + 审计 + 三个关键漏洞修补方案
- `PLAN-bot-channels.md` — 飞书 webhook 适配器（参考 openclaw 架构）+ 会话映射表
- `PLAN-multi-agent.md` — `spawn_subagent` 工具（参考 claude-code-ref AgentTool）+ depth/budget/tool 限制

**调研的关键发现**：

1. **RBAC**：`KnowledgebaseService.accessible(kb_id, user_id)` 存在但**未被调用**在三处关键路径：`dialog_service.async_ask`、`agent_v2_app.create_session`、`agent_v2/tools/rag_retrieve`。这是直接的越权漏洞，P2.1 首要补。

2. **飞书机器人**：RAGFlow 已有 webhook 基建（鉴权/限流/IP 白名单）和 `APIToken.beta` 公共令牌，但没有 IM 特定的签名验证、URL challenge、会话映射。openclaw 的 `FeishuMessageContext` + `buildFeishuConversationId` + `createFeishuReplyDispatcher` 三块可以直接映射成 Python。

3. **Multi-Agent**：claude-code-ref 的 `AgentTool` 用 `runAgent()` 在独立 context 里跑子 `query()`，最后把最终 assistant 文本作为 tool result 返回。我们用 Claude Agent SDK 可以原生支持嵌套 `query()`，不需要另起新协议。

**新建表清单**（Phase 2 全部）：
- `dataset_access` (owner/admin/contributor/viewer 四角色)
- `access_audit_log`
- `bot_channel`
- `bot_conversation_map`
- `bot_message_dedup`（可选，也可以 Redis）
- `agent_v2_subagent_trace`
- `agent_v2_subagent_message`（可选）

全部新表，不改 `knowledgebase` / `user` / `tenant` / `agent_v2_*` 等现有 schema。



### P1.7-C2：知识库详情 shell + sidebar（2026-04-22 深夜 +1）

**已改代码**：
- `web/src/pages/dataset/index.tsx`：wrapper 去掉 `pt-3`，内容区改为 `flex min-h-0 overflow-hidden`，让子页自己控制 padding 和 header
- `web/src/pages/dataset/sidebar/index.tsx`：完全改为企业知识资产侧栏 — 返回全部知识库链接 + 头像/名称/创建日期 + 描述（可截断）+ 「资产概览」三格度量（文档/切片/库体积）+ embedding 模型 + 「知识库工作台」纵向导航，激活态复用全局 sidebar 的 `accent-primary/10` + inset shadow 样式，导航每项补 `data-testid`
- `web/src/locales/zh.ts / en.ts`：新增 `knowledgeList.detailSummary / detailNavigate / backToDatasets / totalSize`

### P1.7-C3：知识库文档表格（2026-04-22 深夜 +1）

**已改代码**：
- `web/src/pages/dataset/dataset/index.tsx`：外层 `Card` 换成 `article`，页头改为企业工作台式 topbar（标题 + 副标题 + `ListFilterBar`），批量操作条下移到 header 内、更紧凑；保留 upload、empty-doc、metadata、bulk-operate、reparse 全部逻辑
- `web/src/pages/dataset/dataset/dataset-table.tsx`：去掉 `absolute bottom-3 right-3` 的分页浮层 BUG，改为 `article > (scroll body) + (sticky footer)` 结构，分页钉到带边框的底栏；空态高度从 `h-24` 扩到 `h-48`
- 保留：`useReactTable` 状态、`useChangeDocumentParser` / `useRenameDocument` / `useShowLog` 弹窗、`data-testid="document-row"`、分页回调

### P1.7-C4：检索测试 Lab（2026-04-22 深夜 +1）

**已改代码**：
- `web/src/pages/dataset/testing/index.tsx`：精简为单一工作台 — 顶部 title + 描述 header，底下双栏 `[420px | 1fr]`，左侧配置栏、右侧结果流，**移除了从未启用的 `count === 1 ? ... : ...` 分支死代码**（`count` 永远是 1）
- `web/src/pages/dataset/testing/testing-form.tsx`：`footer` 加边框 + 背景；question `Textarea` 补 placeholder；新增 `data-testid="dataset-testing-form / -query / -submit"`
- `web/src/pages/dataset/testing/testing-result.tsx`：header 显示结果总数徽标；每条 chunk 改为资产卡片（文档名 + 序号 + 相似度 chip 组 + 高亮 markdown），分页钉到底栏；`from i18next` 改为 hook 形式；过滤 label 从硬编码 `'File'` 改为 `t('knowledgeDetails.fileLogs')`；chunk card 用主题 token 不依赖 `prose` 插件

**验证**：
- 11 个文件 + 新增 locale key，ESLint 通过（仅 `useEffect` 缺 `checkValue` 是上游既有 debt）
- repo-wide TS 错误与 refactor 前同数（全部为上游 legacy debt，本批未新增）
- Vite dev server 上 `/`、`/login-next`、`/datasets`、`/chats`、`/agent-chat`、`/dataset/{dataset,testing,knowledge-graph,dataset-overview,dataset-setting}/{id}`、`/searches`、`/agents`、`/memories`、`/files`、`/profile-setting/profile` 全部 200
- 所有被改 tsx/ts 文件 Vite transform 均 200；HMR 日志无新错误

### P1.7-C5：知识库图谱 / Overview / Setting 页头统一（2026-04-22 深夜 +2）

**已改代码**：
- `web/src/pages/dataset/knowledge-graph/index.tsx`：外层 `Card` → `article`；页头改为 `dataset workbench` 风格 header（标题 + 删除按钮），图谱区改为正确的 `min-h-0 overflow-hidden` 容器，不再用 `absolute right-5 top-5` 浮层
- `web/src/pages/dataset/dataset-overview/index.tsx`：外层 `Card` → `article`，新增 topbar header；保留 `StatCard` / `DatasetFilter` / `FileLogsTable` 全部逻辑与深色图标切换
- `web/src/pages/dataset/dataset-setting/index.tsx`：外层 `Card/CardHeader/CardContent/CardTitle/CardDescription` → 语义化 `article > header + section`，配置标题沿用 workbench 统一字号；同步移除失效的 `Card*` 导入

**验证**：
- ESLint `src/pages/dataset/{knowledge-graph,dataset-overview,dataset-setting}/index.tsx` 仅剩上游 `no-console` / `react-hooks/exhaustive-deps` 遗留 warning，本批未新增
- 15 个核心路由 Vite dev server 全部 200（含 `/dataset/{dataset,testing,knowledge-graph,dataset-overview,dataset-setting}/{id}`）
- 本批文件 Vite transform 均 200，HMR 日志无错误

### P1.7-D1 / D2 / D3 / D4：搜索 / Agent / 记忆 / 文件列表页头统一（2026-04-22 深夜 +3）

**已改代码**：
- `web/src/pages/next-searches/index.tsx`、`web/src/pages/agents/index.tsx`、`web/src/pages/memories/index.tsx`、`web/src/pages/files/index.tsx`：列表页全部应用 `/datasets` 相同的「bordered header + title/subtitle + ListFilterBar + CardContainer + footer pagination」模式；`page-8 / py-6 / py-4` spacing 与 `bg-bg-base / bg-bg-component / bg-bg-component/40` 层级统一；分页条从 `mt-4 px-5 pb-5` 浮层改为底栏风格；空态/搜索空态分支保留
- Files 页把 `FileBreadcrumb` 放到标题下方，未进入子目录时用与其他列表一致的「N · 文件」副标题
- 全部改动未触及业务 hooks（`useFetchSearchList` / `useFetchAgentListByPage` / `useFetchMemoryList` / `useFetchFileList`）与各自的 create / rename / upload / move / bulk-operate 流程

**验证**：
- 16 个核心路由 Vite dev server 全部 200，包括 `/searches`、`/agents`、`/agent-templates`、`/memories`、`/files`
- 4 个文件 ESLint 通过
- HMR 日志无错误（中间有一次 dataset-setting 编辑中过渡态错误，保存后立即 hmr update 恢复）

### P1.7-D5：用户设置 shell + sidebar + 所有子页（2026-04-22 深夜 +4）

**已改代码**：
- `web/src/pages/user-setting/index.tsx`：outer wrapper 去掉 `pt-8` 与 `pr-6 pb-6` 的 RAGFlow 遗留 padding，改为与 dataset wrapper 一致的 `grid-cols-[auto_1fr]` + `bg-bg-base`
- `web/src/pages/user-setting/sidebar/index.tsx`：重做为企业工作台侧栏 — 「返回」链接 + 用户头像/昵称/邮箱 + 「设置」纵向导航（激活态复用 `bg-accent-primary/10 + inset shadow`）+ 底部 `version / 主题切换 / 登出`；每个导航项补 `data-testid`
- `web/src/pages/user-setting/components/user-setting-header/index.tsx`：`ProfileSettingWrapperCard` 与 `Title` 全部改为 `<article>` 布局 + `border-b border-border-button bg-bg-component/60 px-8 py-5` 的统一 workbench 头；`Title` 字号升到 `text-[22px] font-semibold`；DataSource / MCP / Profile / Team 四个子页**自动受益**
- `web/src/pages/user-setting/setting-model/index.tsx`：外层 `div` 改为 `<article>` 结构，顶部补 title header，保留原 3/5 + 2/5 分栏（已配置模型 + 可选模型）
- `web/src/pages/user-setting/setting-api/index.tsx`：从极简 `<ApiContent />` 壳升级为 `<article>` + title header，内容区有 `px-8 py-6` 滚动容器

**验证**：
- ESLint 通过（只剩 `no-console` 上游 warning）
- 17 个核心路由 Vite dev server 全部 200（含 `/user-setting/{data-source,model,team,api,mcp,profile}`）
- 本批文件 Vite transform 均 200，HMR 日志无错误
- 整站 RAGFlow 品牌残留扫描：只剩共享组件名（`RAGFlowFormItem` / `RAGFlowSelect` / `RAGFlowAvatar` / `RAGFlowPagination` / `RAGFlowTooltip`）、OAuth postMessage 协议 ID（`ragflow-google-drive-oauth` 等）、Jira 表单占位符示例（`placeholder: 'RAGFlow'`）—— 均非用户可感知品牌露出，不影响 Phase 1.7 验收

### P1.7 验收结论

用户可见的产品 shell 已完全是「知源 · 企业知识库」：
- 登录页、首页、导航侧栏、所有列表页、知识库详情五页、对话与 Agent 工作台、用户设置六个子页，布局、字号、token、文案一致
- 所有业务能力（数据抓取 / 搜索 / 筛选 / 分页 / 创建 / 重命名 / 批量操作 / 上传 / 移动 / 解析 / 检索测试 / 图谱 / 模型配置 / SSO / MCP）完整保留



### P1.7 审计与回归修复（2026-04-22 深夜）

对活跃的 3 个 UI commit 做整体审计后发现并修复：

- `chat-settings.tsx` 关闭按钮的 `LucidePanelRightClose` 同时挂了 `onClick`，与外层 `<Button onClick>` 叠加导致点击图标时 panel 双向切换不生效 → 去掉 icon 上的冗余 `onClick`
- `chat/styles.css` 中 `.chat-workbench-root` 把浅色态硬编码为 `#fafafa / #1c1917`，偏离全局主题 token → 全部改为 `var(--bg-base)` / `rgb(var(--text-primary))`
- `header.tsx` / `home/index.tsx` / `login-next/index.tsx` 里的硬编码中文改走 `useTranslation`；新增 `header.newAgentSession / agentV2Title / agentV2Description / workspaceSettings / workspaceGroup / buildGroup / docsUrl / helpCenter`、`workspaceHome.*`、`login.tagline / policyCard*` 等 key（en / zh 同步）
- `index.html` `<title>` 从 `RAGFlow` 改为 `知源 · 企业知识库`

**验证**：
- 229 个 repo-wide TS 错误均为上游 legacy debt，本批 11 个文件 + 新增文件本地 0 TS 错误
- ESLint 通过
- Vite dev server `http://127.0.0.1:9222` 上 `/`、`/login-next`、`/datasets`、`/chats`、`/agent-chat`、`/searches`、`/agents`、`/memories`、`/files`、`/profile-setting/profile` 全部 200
- 源模块（header、nav、product-mark、home、login、chat index/styles/sessions/single-chat-box/chat-settings）Vite transform 全部 200

### P1.7-C 首批：`/datasets` 列表完成（2026-04-22 深夜）

**已改代码**：
- `web/src/pages/datasets/index.tsx`：页头改为知源风格，标题 + 副标题 + 资产统计三联（知识库 / 文档 / 切片），保留 `ListFilterBar`（搜索、所有者筛选）与创建按钮；分页区移入底部边框条
- `web/src/pages/datasets/dataset-card.tsx`：放弃共享 `HomeCard`，改为自管的企业资产卡 — 头像 + 名称 + 团队/仅自己 badge + 归属、两段式描述、`document_count / chunk_count` 度量栏、embedding 模型 + 更新时间页脚，整卡 hover 高亮 + 键盘 Enter/Space 可达
- `web/src/locales/zh.ts` / `en.ts`：新增 `knowledgeList.listSubtitle / metricDocuments / metricChunks / embeddingModel / updatedAt / permissionTeam / permissionMe / ownerPrefix`

**保留**：
- `useFetchNextKnowledgeListByPage`、`useSelectOwners`、`useSaveKnowledge`、`useRenameDataset`、`useNavigatePage` hooks
- 分页、搜索（含防抖）、所有者筛选、创建 / 重命名 / 删除弹窗、`isCreate=true` 自动打开创建弹窗
- `data-testid` 全部保留（`datasets-list`、`datasets-create`、`dataset-card`、`dataset-name`）

**验证**：
- 本批 ESLint 通过，本批 TS 无新增错误
- Vite transform `/src/pages/datasets/index.tsx`、`/src/pages/datasets/dataset-card.tsx`、`/src/locales/*.ts` 均 200
- `/datasets` 整页入口 200

### P1.7 当前任务（2026-04-22）

用户明确目标：当前前端仍然完全是 RAGFlow 前端，需要包装成我们自己的企业知识库项目。项目内参考稿为 `design-refs/zhiyuan/`，应参考其「知源 · 企业知识库」设计语言重构整个前端，但保留全部功能，只重构前端和 UI。

**已新增文档**：
- `PRODUCT-UI-PLAN.md` — Phase 1.7 企业知识库前端产品化重构计划

**执行策略**：
- 已完成全局可见外壳：`web/src/layouts/*`、`web/src/pages/login-next/*`、`web/src/pages/home/*`
- 当前统一高频工作台体验：`web/src/pages/agent-chat/*`、`web/src/pages/next-chats/chat/*`
- 保留所有已有 routes 和业务 hooks
- 后续逐批改知识库、文档、搜索、文件、设置等页面

### P1.7-A 已完成首批改造（2026-04-22）

**已改代码**：
- 新增 `web/src/layouts/components/product-mark.tsx`：统一「知源 / 企业知识库」品牌标识
- 重构 `web/src/layouts/components/header.tsx`：移除显眼 RAGFlow / Discord / GitHub 外部入口，保留语言、帮助、主题、通知、用户设置
- 重构 `web/src/layouts/components/global-navbar.tsx`：导航改为「概览 / 知识库 / 对话 / Agent / 检索 / 编排 / 记忆 / 文件」，保留全部原路由
- 重构 `web/src/pages/login-next/index.tsx`：登录页改为企业知识库入口，保留登录、注册、SSO、禁用密码登录等逻辑
- 重构 `web/src/pages/home/index.tsx`：首页改为企业知识库概览 + 快速入口 + 原知识库/应用列表
- 更新 `web/src/locales/zh.ts` / `en.ts`：核心产品文案从 RAGFlow 转为企业知识库
- 更新 `web/src/global.less`、`page-container.tsx` 的基础视觉（后续已恢复默认暗色主题兼容）

**验证**：
- 本次改动文件 targeted ESLint 通过
- `npm run type-check` 未通过，但失败来自仓库既有大量 TS 债；本次新增的唯一未使用导入已修复
- 已启动前端 dev server：`http://127.0.0.1:9223/`
- Vite 已成功转换并返回首页、登录页、Header、Nav 模块（HTTP 200）

### P1.7-A 暗色主题兼容修正（2026-04-22）

用户反馈：切到全白背景后，原本按暗色主题设计的组件出现对比度/层级问题。

**修正**：
- `web/src/app.tsx` 默认主题恢复为 `ThemeEnum.Dark`
- 移除首批改造里的硬编码白底/黑字：`bg-white`、`#fafafa`、`#1c1917` 等改为现有主题 token
- 新增 `.zy-grid-bg`，登录页背景跟随 `--bg-base` / `--border-button`
- Header / Nav / 首页 / 登录页统一使用 `bg-bg-base`、`bg-bg-component`、`bg-bg-card`、`text-text-primary`、`text-text-secondary`、`border-border-button`、`accent-primary`

**验证**：
- targeted ESLint 通过
- `git diff --check` 通过
- Vite 成功转换首页、登录页、Header、Nav 模块（HTTP 200）

**下一步入口**：
- P1.7-B：统一 `/agent-chat` 与新全局 shell 的视觉细节
- P1.7-C：开始重构 `/datasets` 和 `/dataset/**`，这是 RAGFlow 痕迹最重的核心业务区

### P1.7-A 二轮整体翻新（2026-04-22）

用户反馈：暗色兼容后已经不错，但整体仍有些像 RAGFlow。参考 `design-refs/zhiyuan/project/ui.jsx` 的 Sidebar / Topbar 模式后，第二轮把全局 shell 从 RAGFlow 风格顶部胶囊导航改成「知源」参考稿的左侧企业工作台导航。

**已改代码**：
- `web/src/layouts/root-layout.tsx`：整体布局从顶部 header + main 改为左侧 sidebar + 内容区
- `web/src/layouts/components/header.tsx`：重构为产品 sidebar，包含品牌、新建 Agent 会话、Agent v2 说明、语言/帮助/主题/通知、用户设置入口
- `web/src/layouts/components/global-navbar.tsx`：重构为纵向导航，分为 Workspace / Build 两组，保留全部原路由

**验证**：
- targeted ESLint 通过
- `git diff --check` 通过
- Vite 成功转换 root layout、sidebar shell、sidebar nav、home 模块（HTTP 200）

### P1.7-B 对话页向 Agent 工作台对齐（2026-04-22）

用户反馈：`/agent-chat` 和原「对话」页面前端风格差别太大，需要将对话页面风格向 Agent 对齐。

**已改代码**：
- `web/src/pages/next-chats/chat/index.tsx`：对话详情页改为 Agent 风格工作台结构：全局 shell + 左会话栏 / 中消息区 / 右设置栏
- `web/src/pages/next-chats/chat/styles.css`：新增对话工作台样式 tokens，复用暗色主题变量
- `web/src/pages/next-chats/chat/sessions.tsx`：会话列表边框、背景、选中态向 Agent 会话栏靠齐
- `web/src/pages/next-chats/chat/chat-box/single-chat-box.tsx`：消息区和输入框收窄到更接近 Agent 的阅读宽度
- `web/src/pages/next-chats/chat/app-settings/chat-settings.tsx`：右侧设置栏改为 Agent 工具栏类似的边框/面板层级
- `web/src/pages/next-chats/chat/chat-box/next-multiple-chat-box.tsx`：多模型调试页输入区宽度和工作台 spacing 对齐

**验证**：
- targeted ESLint 通过
- `git diff --check` 通过
- Vite 成功转换 chat detail、sessions、single chat box、settings 模块（HTTP 200）

### M1.6 完成内容（2026-04-22）

| Step | 内容 | Commit |
|---|---|---|
| Step 1 | 去除 env var 偷懒，接回 RAGFlow 模型供应商（TenantLLM）| 470e31e / 3418bad |
| Step 3 | Markdown 内联 [N] 脚注 + 点击滚动高亮对应 chunk | 4c45929 |
| Step 2 | Agent 模板系统（6 个预置：保障房/政策/法务/研报/客服/Wiki）| 824ee28 |

**新增端点**：
- GET `/v1/agent_v2/model` — 返回用户 TenantLLM 里的 Chat 模型列表
- GET `/v1/agent_v2/template` — 返回 6 个预置 Agent 模板

**NewSessionDialog 变化**：
- 顶部新增「从模板开始」2 列卡片区
- 模型下拉从硬编码变成"动态拉 /model 端点"（只列你配过的）
- System Prompt 默认模板加入 [N] 引用规范

**移除的 debt**：
- ❌ `AGENT_V2_DEEPSEEK_KEY` / `AGENT_V2_ANTHROPIC_KEY` 环境变量依赖
  （保留作 fallback，但正常使用不再需要）

### 残留的小 debt（Phase 2 再说）
- session/conversation 表和原版 Dialog 分离（Phase 2 考虑统一）
- Langfuse 可观测性还没接（RAGFlow 有依赖但 Agent v2 没接）

### M1.5 验收结果（2026-04-22）

**10 题评测均分 4.8 / 5，全部类别通过**：
- 事实题 (Q1-Q3) 均分 4.67 ≥ 4.0 ✅
- 推理题 (Q4-Q7) 均分 4.75 ≥ 3.8 ✅
- 防幻觉题 (Q8-Q10) 均分 5.00 ≥ 4.5 ✅ ⭐ 三题全满分

详见 `test/e2e/baojian_house_results_20260422.md`（含每题完整答复 + 工具链 + 涉及文件 + 人工打分）。

**性能**：总耗时 356s / 总成本 $1.127 / 平均每题 35.6s / 平均 3.2 次工具调用

### 残留小优化（暂不阻塞 Phase 1）

| 优化点 | 严重度 | 行动 |
|---|---|---|
| Q3 答复可以更明确"不得上市交易" | 低 | System Prompt 补一句，M1.6 再调 |
| 脚本输出截断（Q4 超过 2000 字被截） | 低 | 改 scripts/run_baojian_golden.py 不截断 |
| Q8 调了 4 次工具才确认没首付规定 | 极低 | 可接受（宁可多查） |

这些都不影响 Phase 1 验收，M1.6 顺手改。

### M1.3-M1.5 期间修复的 3 个前端 bug（M1.4 后发现）

- tool_call 实时不刷新 → ContextVar immutable update
- Markdown 未渲染 → react-markdown + remark-gfm
- 引用来源缺失 → ReferencesList 从 rag_retrieve 结果抽取文件+chunks

### M1.4 验收结果
- ✅ 新页面 `web/src/pages/agent-chat/`（3 列布局 + 设计系统）
- ✅ 设计语言：Linear/Vercel 风格，teal 品牌色 #0f766e，Inter Tight 字体
- ✅ 3 列布局：会话侧栏 260px / 消息流 / 工具调用侧栏 340px
- ✅ 组件：SessionSidebar / MessageList / ThinkingBlock / ToolCallCard / ToolCallsSidebar / Composer / NewSessionDialog
- ✅ SSE 消费：原生 fetch + EventSourceParserStream
- ✅ 路由 `/agent-chat` 已注册 + 主导航新增"工作台"入口
- ✅ 双语 i18n（en / zh）
- ✅ TypeScript 0 errors（本模块）

### 可访问
打开 http://localhost:9222/agent-chat 登录后即可用。

### 体验路径（Phase 1 M1.4 完成后即可）
1. 硬刷新浏览器 `Cmd+Shift+R`
2. 顶部菜单点「工作台」（或直接访问 /agent-chat）
3. 左侧「新建会话」：
   - 名称随便（如"保障房政策顾问 v2"）
   - 知识库选「深圳保障房政策库」
   - 模型选 DeepSeek（服务端已配 AGENT_V2_DEEPSEEK_KEY）
   - System Prompt 默认已包含严格防幻觉指令
   - 创建
4. 聊：问「公共租赁住房申请条件和社保年限」之类的问题
5. 观察右侧工具调用侧栏：每次 rag_retrieve 的 args + 召回结果都在

### M1.5 入口（下一步）
1. 建 3 道黄金用例文档 `tests/e2e/baojian_house.md`
2. 对保障房场景跑 10 个问题，人工打分
3. 按打分结果调 System Prompt / Tool 描述 / top_n / 阈值
4. Langfuse 接入（RAGFlow 已内置 langfuse 依赖，需创建 tracer）

### M1.3 验收结果
- ✅ 3 张 DB 表（agent_v2_session / message / tool_call）自动建表
- ✅ Session Service 3 个 pytest 通过（真 MySQL 读写）
- ✅ Blueprint `/v1/agent_v2/*` 自动注册（session CRUD + tool list + SSE conversation）
- ✅ End-to-end persistence：session 1 行 + messages 2 行 + tool_call 1 行
- ✅ Agent 自主调工具 + DeepSeek 答复 + 三表落库一气呵成
- 实测：单轮 46s / 1 次 rag_retrieve / $0.12 / 答复 791 字

### M1.2 验收结果
- ✅ 4 个工具全部实现：`rag_retrieve` / `rag_list_docs` / `rag_read_doc` / `rag_graph_query`
- ✅ 直接调用 smoke test 全过（list 到 22 文件 / retrieve 相似度 0.52 / graph 无图谱 KB 返回空）
- ✅ pytest 单元测试 **37/37 通过**（base 9 / event 9 / registry 5 / schemas 14）
- ✅ 工具输出 32KB 截断机制生效
- ✅ `tools/README.md` 文档完整

### M1.1 验收结果
- ✅ Claude Agent SDK 集成成功（Python SDK 0.1.64）
- ✅ DeepSeek 通过 `https://api.deepseek.com/anthropic` 端点可用
- ✅ 工具 `rag_retrieve` 连通 RAGFlow Dealer.retrieval()
- ✅ 保障房场景 demo：Agent 自主 5 次检索 → 综合答复准确（社保 3 年 + 特殊家庭豁免均正确）
- ✅ ContextVar 机制成功（解决 In-Process MCP server 跨进程 env 传不过来的问题）
- 实测：114s / $0.148 / 5 次 tool call

### 可运行的命令
```bash
cd ~/Opensource/forks/ragflow
export AGENT_V2_PROVIDER=deepseek DEEPSEEK_API_KEY=sk-... NLTK_DATA=./nltk_data
.venv/bin/python scripts/test_agent_v2.py \
  --question "你的问题" --max-turns 6 --log-level WARNING
```

---

## 环境状态（验证过可用）

| 服务 | 地址 | 状态 |
|---|---|---|
| 前端（Vite） | http://localhost:9222 | ✅ |
| 后端（Flask） | http://localhost:9380 | ✅ |
| Task Worker | `rag/svr/task_executor.py 0` | ✅ |
| MySQL | 容器 `docker-mysql-1` | ✅ healthy |
| Elasticsearch | 容器 `docker-es01-1` | ✅ healthy |
| MinIO | 容器 `docker-minio-1` | ✅ healthy |
| Redis | 容器 `docker-redis-1` | ✅ healthy |

**重启方法**：见 `CLAUDE.md` 「macOS 本地开发」段。

## 模型接入状态

| 类型 | 提供方 | 模型 | 状态 |
|---|---|---|---|
| Chat | DeepSeek | `deepseek-chat` | ✅ |
| Embedding | Ollama（本地） | `bge-m3` | ✅ |
| Rerank | — | — | 未接 |
| Vision | — | — | 未接 |

DeepSeek API Key 已配（在 RAGFlow 内部 MySQL）。

## 数据状态

| 知识库 | 文档数 | Chunk 数 | 用途 |
|---|---|---|---|
| 深圳保障房政策库 | 22 | 231 | Phase 0 验证场景 |

**源文件**：`~/Opensource/kb-data/深圳保障房政策/`

## 已完成（按时间倒序）

### 2026-04-21
- ✅ 工作区索引 + 规范更新：`kb-data/` 目录登记进 `~/Opensource/CLAUDE.md` 和 `~/Opensource/ai-dev-guide.md`
- ✅ 政策数据归档到 `~/Opensource/kb-data/深圳保障房政策/`（22 份文件 + 1 份原始压缩包）
- ✅ 建助手测试原版 Dialog 模式：**发现严重幻觉**（保租房"本科 45 岁/专科 35 岁"是编的，政策原文是"人才安居办法"里的 30/35/40 岁）
- ✅ 核对 AI 回答 vs 政策原文：公租房条款基本正确；配售型"无房满 3 年"、共产房"无房满 5 年"是对条文的误读（原文是"3/5 年内未转让过"）
- ✅ 数据库直修绑定 KB 到 Dialog（UI 保存未生效的坑）
- ✅ 装 openjdk@17，修复 Tika 启动（macOS 原生无 Java 自动解析 DOC/DOCX 的链路）
- ✅ 启动 `task_executor.py` worker（之前漏启，文档全部卡在排队）
- ✅ 修复 graspologic 从 gitee 拉不稳的问题（`pyproject.toml` 加 `[tool.uv.sources]` 指向 github）
- ✅ RAGFlow 首次本地跑通（macOS ARM64）：backend 9380 + frontend 9222

## 下次继续（入口明确）

### 第一优先：Phase 0 — Agent with Tools demo

打开前端 http://localhost:9222/ → 登录 → **Agent** 菜单 → **+ 创建 Agent**。

**建议步骤**：

1. 模板选 "空白" 或 "Your Starter Dataset Chatbot"
2. 画布放一个 `Agent with Tools` 节点（**不是**普通 LLM 节点）
3. 点节点 → 右侧配置：
   - 模型：`deepseek-chat`
   - 温度：`0.1`
   - System Prompt 用下面这段 ↓
4. 给节点挂工具：
   - `Retrieval` → 深圳保障房政策库（必挂）
   - `Tavily` → 如果有 Key 就挂（对比"没查到 + 外部搜索"效果）
   - `ExeSQL` → 可选
5. 保存 → 发布 → 右上角 Demo 测试

### System Prompt（复制用）

```
你是深圳保障房政策顾问。你的能力边界严格限定为深圳保障房相关政策。

工作方式：
1. 用户问题来了，先判断：这是否和保障房政策有关？
   - 无关（闲聊/其他领域）：直接说"我只负责深圳保障房政策咨询"，不调工具
   - 有关：继续第 2 步

2. 调用 Retrieval 工具检索相关政策条款：
   - 第一次检索：用用户问题的核心关键词
   - 如果召回片段信息不足：换关键词再调一次（最多 3 次）
   - 必要时用不同角度查：例如"社保年限"和"申请条件"分别查

3. 严格基于检索到的原文回答：
   - 所有数字、年限、百分比、面积必须有原文支撑
   - 原文没写的内容，禁止用训练知识补充
   - 未查到的部分直接说"未查到相关规定，建议向当地住建部门咨询"

4. 回答结尾附「本回答依据：」+ 列出检索到的政策文件名

禁止行为：
- 编造年龄、学历、社保年限等数字
- 把其他城市的政策套用到深圳
- 把"X 年内未转让过"说成"X 年无房"
```

### 测试 3 道题（跑完贴结果给我）

```
① 事实题：
   保障性租赁住房和公共租赁住房有什么区别？
   （预期：分两类、各引不同《管理办法》原文）

② 推理题：
   我是深户、已婚、无房、3 人家庭（含 3 岁小孩）、月收入 2 万。
   我能申请哪些类型的保障房？按优先级列出。
   （预期：综合多份文件；提到"需查当年收入限额"；不编社保年限）

③ 防幻觉题：
   申请配售型保障房，首付比例多少？过户年限多久？
   （预期：说"未查到相关规定"，而不是编数字）
```

### 观察点

- Agent 每轮调了几次工具？（看调用链/日志）
- 调用关键词对不对？（看 tool_input）
- 引用的原文是否精准？
- 是否还有幻觉？（对比 STATUS.md 历史记录里 Dialog 模式的错）

### Phase 0 退出条件

3 道题的回答质量明显优于之前 Dialog 模式（特别是防幻觉题），决定：
- ✅ Agent-first 路径靠谱 → 进入 Phase 1（按 PLAN.md 和 DESIGN.md 写代码）
- ⚠️ 效果仍不理想 → 调 Prompt / 加工具 / 换 top_n 和阈值再跑一次
- ❌ 根本不行 → 重新审视方案（回头和我讨论）

## 如果接下来要开 Phase 1

**入口文件**：
- `PLAN.md`（总体路线）
- `DESIGN.md`（Phase 1 架构、API 设计、DB schema）
- `FORK.md`（与上游的关系、哪些文件易冲突）

**从这里开始写代码**：
```bash
cd ~/Opensource/forks/ragflow
git checkout -b feat/agent-v2
mkdir -p api/agent_v2/tools
touch api/agent_v2/__init__.py api/agent_v2/runner.py api/agent_v2/registry.py
```

**里程碑 M1.1**：在 `api/agent_v2/runner.py` 用 Claude Agent SDK 跑通第一个工具调用。
验证：能在终端里 `.venv/bin/python -c "from api.agent_v2.runner import run; run(...)"` 看到工具被调用。

## 阻塞与待决（目前为空）

无。

## 快速命令清单

```bash
# 进项目
cd ~/Opensource/forks/ragflow

# 看各服务状态
docker ps --filter name=docker-
lsof -nP -iTCP:9380 -sTCP:LISTEN
lsof -nP -iTCP:9222 -sTCP:LISTEN

# 看后端日志
tail -f logs/backend.log

# 看 worker 日志
tail -f logs/task_executor.log

# 查 MySQL（看 Dialog/KB/Document 数据）
docker exec docker-mysql-1 mysql -uroot -pinfini_rag_flow -Drag_flow --default-character-set=utf8mb4

# 直接查 ES（绕过 UI 看 chunks）
curl -s -u elastic:infini_rag_flow http://localhost:1200/ragflow_968bd6ec3c9f11f1afc91f3c182e7a61/_count
```
