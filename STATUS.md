# RAGFlow Agent 二改 — 进度快照

> **STATUS.md 是会话交接文档**，每次有实质进展时更新，只记录"当前状态 + 下一步入口"，不是历史日志。
> 老条目归档到 `archive/HISTORY-phase-2.x.md`。STATUS 保持 ≤ 5 个 "最近更新" 段。

---

## 最近更新：2026-04-25（Phase 2.8 **v1.0 落地** — Prompt 系统架构重写）

**S1-S7 全部完成**。`AUDIT-claude-code-alignment.md` §五-e 列的 6 偏差 D1-D6 全部
得到结构性修正。

### 落地总览

| Stage | 内容 | 状态 |
|---|---|---|
| S1 | `api/agent_v2/tools/_names.py` 创建 + registry / annotations / SEARCH_HINT_BY_TOOL 切到常量；3 个 dict 22 项全部 parity | ✅ |
| S2 | `prompting/builder.py` 重写：`PromptSection` / `PromptCtx` / `PromptCache` / `assemble_prompt` / `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` / `prepend_bullets`；旧 `build_supervisor_prompt` / `build_subagent_prompt` 保留作 backward-compat | ✅ |
| S3 | `prompting/sections.py` 共享段库（590 行）：identity / mission / domain_context / strict_rag_constraints / read_only_block / plan_gate_block / retrieval_output_rules / no_citation_for_operators / step_marker_rules / tone_and_style / numeric_length_anchors / actions_risk / using_your_tools / supervisor_workflow / supervisor_delegation / clarify_vs_act + 3 个 dynamic 段（pending_plan / kb_scope，v0.15 真接通时启用）+ 3 个 preset assembler（supervisor / operator / reflective） | ✅ |
| S4 | 4 subagent 重构：sub_archivist 192→91 行 / sub_librarian 145→153 行 / sub_evidence_checker 98→118 行 / sub_policy_researcher 94→112 行；全部 v2.0.0 | ✅ |
| S5 | `_common.py::strict_rag_prompt` 改为 callable + 6 supervisor 全部走新路径；版本号 v1.0.0 → v2.0.0 | ✅ |
| S6 | 测试：新 `test_prompting_sections.py`（27 case）+ `test_sub_archivist.py` 适配 callable system_prompt；560 passed / 8 skipped；ruff 0 错 | ✅ |
| S7 | 静态 prompt 长度测量 + 文档收尾 | ✅ |

### 量化结果

**prompt 大小（resolve_system_prompt 渲染后）**：

| Agent | v1.x → v2.0 | Δ |
|---|---:|---:|
| sub_archivist | 7804 → **3248** | **-58%** |
| supervisor_baozhang | 6962 → **6080** | -13% |
| sub_librarian | 4677 → 5398 | +15%（+ READ_ONLY 块、actions、长度锚点）|
| sub_evidence_checker | ~3000 → 4670 | +56%（同上）|
| sub_policy_researcher | ~3000 → 4611 | +54%（同上）|
| 6 supervisor 平均 | ~6000 → ~5900 | 持平 |

read-flavored agent 的 prompt 略胖是因为加进了之前没有的共享段（`READ_ONLY_BLOCK`、
`actions_risk`、`tone_and_style`、`numeric_length_anchors`）——内容是真增益（行为
更一致、可缓存），且 v0.15 真接通 cache 后跨 agent 共享。**sub_archivist 一项的
58% 缩减就证明了去重的价值**。

**代码大小**：

| 文件 | 之前 | 之后 |
|---|---:|---:|
| sub_archivist.py | 192 行 | 91 行 |
| 4 subagent 总行数 | 529 行 | 474 行 |
| 6 supervisor 平均 | ~50 行 | ~48 行（基本不变，behaviour 由 _common 改 callable 实现）|
| **新模块 _names.py** | — | 135 行 |
| **新模块 sections.py** | — | 590 行 |
| 增量净行数 | — | +540 |

### 6 偏差修正状态

| # | 偏差 | 修正 |
|---|---|---|
| **D1** 静态/动态分界 + 节级缓存 | `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 常量 + `PromptCache` 实现；assemble_prompt 在 boundary 标记前后切分。**v1.0 暂未真接通 SDK cache_control**（DeepSeek server-side 自动检测前缀），架构层已就位 | ✅ 架构 / ⏳ 真接通 v0.15 |
| **D2** enabledTools 过滤 | `PromptSection.compute(enabled_tools, ctx)` 标准签名；`make_plan_gate_block` / `make_supervisor_delegation_section` / `make_clarify_vs_act_section` / `make_step_marker_rules` 都按 `enabled_tools` 决定是否 emit；段返 None 自动消失 | ✅ |
| **D3** 工具名常量化 | `tools/_names.py` 22 工具 + 5 group tuple；registry / annotations / SEARCH_HINT_BY_TOOL / runner._EMPTY_RAG_TOOLS / 6 supervisor / 4 subagent 全部 import；裸字面量 grep 命中数从 50+ 降到 0（@tool 装饰器自身 name 字段除外，那是工具的 canonical 自定义） | ✅ |
| **D4** 段函数化 + null 自动消失 | `PromptCache.resolve` 自动过滤 None；`make_identity_section` / `make_mission_section` / `make_domain_context_section` 等多个段都会在条件不满足时 return None | ✅ |
| **D5** Tool 信息单一来源 | `_SUBAGENT_SKELETON` / `_SUPERVISOR_SKELETON` 模板里的 "Tool cost hints" 段已被新 preset 移除（preset 不再包含 cost hints）。MCP annotations + searchHint 是唯一来源 | ✅ |
| **D6** 数字化长度锚点 | `make_numeric_length_anchors`：≤25 words inter-tool / ≤100 words final / ≤30 words per [step K/N done] line。supervisor / operator / reflective 三个 preset 全包含 | ✅ |

### 测试

- `pytest test/agent_v2/`：**560 passed / 8 skipped**（v0.13 时 478，新增 82：27 个新 prompt section + 55 来自 parametrized test_runner_registry / test_subagent_event_bubble 重新计数）
- `uvx ruff check api/agent_v2/ test/agent_v2/`：**0 errors**
- 工具名字面量 grep：仅 `@tool(name="..."` 装饰器自身 ~6 处（canonical 工具自定义，等价于 claude-code-ref 每个工具的 `*Tool/toolName.ts`）

### 关键决策记录

1. **保留 backward-compat**：`build_supervisor_prompt` / `build_subagent_prompt` 旧函数没删，下个 phase（v0.15）再清。
2. **dynamic 段在 v1.0 暂为空**：`pending_plan_section` / `kb_scope_section` 已写好但 `supervisor_dynamic_sections()` 返 `[]`。原因是 `AgentDefinition.system_prompt` 现在仍是 module-load-time 一次性求值；要 per-turn 真动态需要把求值挪到 runner.run() 里，留 v0.15 做。boundary marker 仍存在，让未来真启用时无需改 caller。
3. **subagent 文件不再放 inline 的 HARD_RULES / WORKFLOW / TOOL_RULES / OUTPUT_RULES list**：sub_archivist 现在只有 `mission` 字符串 + `_build_*_system_prompt` callable。复杂的 librarian / evidence_checker / policy_researcher 用 `make_*_workflow_section()` inline 加一段定制 workflow，其余共享。
4. **callable system_prompt**：schema 早就支持 `Callable[[dict], str]`，本次首次真用上。`AgentDefinition.system_prompt = _build_archivist_system_prompt`，不再是字符串字面量。

### 下一步入口

1. **v0.14 commits 拆分**（4 commits）：docs / 工具名常量 + builder + sections / 10 definition / 测试 + STATUS。git status 确认无脏工作树
2. **真机 smoke**：浏览器里跑一次 sz-baojian-house supervisor 让 LLM 选工具，对照 v0.13 行为
3. **v0.15 候选**：
   - 把 system_prompt 求值挪到 `AgentRunner.run()` 让 dynamic 段真起作用
   - `pending_plan_section` / `kb_scope_section` 真动起来
   - 接通 SDK cache_control / DeepSeek prompt_cache 让 boundary 真省 token

---

## 最近更新：2026-04-25（Phase 2.8 **立项** — Prompt 系统架构重写）

**触发**：v0.13 落地后用户审视 Agent 系统提示词，发现 v0.3 立的 "8 段式 prompt"
只是抄了节标题没抄到 claude-code-ref 真正核心机制。深读
`vendor/claude-code-ref/src/utils/systemPrompt.ts` +
`src/constants/prompts.ts:448-581` + `src/constants/systemPromptSections.ts` +
`packages/builtin-tools/src/tools/AgentTool/built-in/exploreAgent.ts`，锁定 6
个偏差（D1-D6）。

**6 个偏差**（详见 `AUDIT-claude-code-alignment.md` §五-e）：

1. **D1 静态/动态分界 + 节级缓存**：claude-code-ref 用
   `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 把可缓存静态段和易变动态段切开 +
   `systemPromptSection(name, compute)` memoize；我们整 prompt 字符串拼接，
   DeepSeek prompt_cache 命中率 0%
2. **D2 enabledTools 过滤**：ref 用 `Set<string>` 决定段是否引用某工具；
   我们硬编码 `"get_pending_plan"` 等字面量
3. **D3 工具名常量化**：ref 全部 `BASH_TOOL_NAME` import；我们 22 工具裸字面量
   散落 50+ 处
4. **D4 段函数化 + null 自动消失**：ref 段返 `string | null`，filter 掉；
   我们大字符串模板，每个 slot 必须有内容
5. **D5 Tool 信息单一来源**：ref 工具描述 + system 段职责不重；我们 prompt 段
   重述 tool description，且加了第三处 "Tool cost hints" 表
6. **D6 数字化长度锚点**：ref `"≤25 words / ≤100 words"` 实测 1.2% token 减少；
   我们全定性 "be concise"

**核心产物**（待落地）：
- `api/agent_v2/tools/_names.py` — 22 工具名常量集中
- `api/agent_v2/prompting/builder.py` 重写 — `PromptSection` / `PromptCtx` /
  `PromptCache` + `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`
- `api/agent_v2/prompting/sections.py` — 共享段库（IDENTITY / READ_ONLY_BLOCK /
  PLAN_GATE_BLOCK / CITATION_RULES / TONE_AND_STYLE / NUMERIC_LENGTH_ANCHORS /
  PENDING_PLAN 等）
- 10 个 AgentDefinition 切到 `static_sections=[...] + dynamic_sections=[...]`
- 测试 `test_prompting_sections.py` + 现有断言适配

**预期改变**：

| 指标 | 当前 | 预期 |
|---|---:|---:|
| sub_archivist.py | 192 行 | ~80 行 |
| sub_archivist 渲染后 prompt | ~3.4K char | ~2.0K char |
| 静态段缓存命中率（同 session）| 0% | 70-80% |
| 工具名 grep 命中数 | 50+ | 1（`_names.py`）|
| pytest | 478 passed | 478 + ~25 新增 |

**Stage 拆解**（11–14h）：S1 _names.py（1h）→ S2 builder 重写（3h）→
S3 sections.py（3h）→ S4 4 subagent（2h）→ S5 6 supervisor（1.5h）→
S6 测试（2h）→ S7 smoke + 文档（1h）→ S8 commit 拆分（0.5h）

**已落地**：
- `AUDIT-claude-code-alignment.md` §五-e — 6 偏差 D1-D6 + T19-T27 整改清单
- `PLAN-prompt-architecture.md` — 完整设计 + ref 引用精确到行号 +
  backward-compat 策略 + 风险 / 缓解
- `PLAN.md` 注册 Phase 2.8 + v1.0 版本记录
- 本 STATUS 顶部条目；老条目（04-23 v0.4-v0.6 / v0.5 / v0.4 / v0.3 / v0.2 /
  v0.1 / 上架硬化 / P2.5-hardening / Phase 2.5 / P3.1 / P2.3 / P2.2 / P2.1）
  搬到 `archive/HISTORY-phase-2.x.md`，STATUS.md 从 1247 行降到 ~200 行

**下一步入口**：
1. S1 开工：建 `api/agent_v2/tools/_names.py` 集中 22 工具名
2. S2 重写 `prompting/builder.py`：`PromptSection` 抽象 + cache +
   `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`；旧 `build_supervisor_prompt` /
   `build_subagent_prompt` 内部转调新 API（保 backward compat）
3. S3 起步：`prompting/sections.py` 共享段库

---

## 最近更新：2026-04-25（Phase 2.7 **v0.10 完整落地** — 附件协议 + 下载-验证-归档）

**当前阶段**：Phase 2.7 全 5 stage 闭环，两个核心用户场景端到端可用。

### 落地总览

| Stage | 内容 | 关键文件 |
|---|---|---|
| 1 | DB + HTTP + ToolContext 附件基础设施 | `db_models.AgentV2Attachment` / `AgentV2AttachmentService` / `POST-GET-DELETE /v1/agent_v2/session/<id>/attachments` / `api/agent_v2/attachments.py` |
| 2 | 2 新 MCP 工具 | `doc_archive_attachment` (sub_archivist, plan_gated) + `web_fetch_to_attachment` (sub_archivist, openWorld) |
| 3 | `submit_plan.preview` 扩展 | 8KB markdown_excerpt + 前端 `pending-plan-card` 折叠渲染 |
| 4 | 前端 composer 附件 UI | 📎 按钮 + drag-drop + `AttachmentChip` + `useAttachments` hook |
| 5 | 真机 smoke + 文档 | 两 smoke 脚本 + G13 8 个人工用例 + FORK.md v0.10 |

### 两个场景端到端

**场景 1 — 用户上传 → Agent 归档**：
```
composer 拖 PDF → POST /attachments（MinIO + DB staged 行 + 8KB preview）
→ 用户："归档到 XX KB" → POST /conversation（send_message 自动扫 staged 附件注入 ctx）
→ supervisor 看到 "# 会话附件" 段 → spawn_subagent(sub_archivist)
→ archivist: submit_plan(preview=attachment.preview_text[:2000])
→ 前端 plan card 弹可折叠预览
→ 用户批准 → get_pending_plan → doc_archive_attachment → KB
```

**场景 2 — URL 下载 → 用户验证 → 归档**（与场景 1 共用最后一步）：
```
用户："把 https://... 归档" → supervisor spawn sub_archivist
→ archivist: web_fetch_to_attachment(url)（SSRF 三段防御 + redirect 白名单 + markdownify）
→ submit_plan(preview={kind:"markdown_excerpt", excerpt, source_ref: url})
→ 用户批准 → doc_archive_attachment（复用）
```

### 关键设计决策（对齐 claude-code-ref）

1. **Attachment 作独立 message type** — 对齐 `attachments.ts:3675` 的 60+ 子类型
2. **复用 submit_plan + plan_gate 作 staging** — ref 全库无 PendingQueue 抽象
3. **场景 1 / 2 共用 `doc_archive_attachment`** — 两场景最后一步相同
4. **图片走 OCR 路径**（不走 Vision inline）— 持久可检索；`FileType.VISUAL` 交给上游 PaddleOCR/MinerU
5. **MinIO bucket 重命名** `agent_v2_attachments` → `agent-v2-attachments`（S3 DNS-compliant naming）

### 验证

- **自动化测试**：`pytest test/agent_v2/` → **452 pass / 8 skip**（376 → +76）
  - `test_attachments.py` 36 / `test_doc_archive_attachment.py` 17 / `test_web_fetch_to_attachment.py` 11 / `test_submit_plan_preview.py` 12
- **lint**：`uvx ruff check api/ test/ scripts/` clean
- **前端**：`npm run lint` clean + `npm run build` 51.6s OK
- **真机 smoke（DeepSeek）**：
  - `scripts/smoke_attachment_archive.py` → **PASS ✓**（上传 → staged → supervisor 看到 → spawn archivist）
  - `scripts/smoke_web_download_archive.py` → **PASS ✓**（URL 归档指令 → supervisor clarify → confirm → spawn archivist）
- **人工用例**：TEST-MANUAL-v0.4.md 加 G13（8 个用例，P0-P2）

### 工具总数

20 → **22**（新 `doc_archive_attachment` + `web_fetch_to_attachment`）

### 下一步入口

1. （可选）真机手工跑 G13-004 / G13-005 端到端，拿到一份 archive 成功的截图
2. （可选）子 agent 事件 bubble up 机制完善——smoke 2 无法看到子 archivist 的 `plan_submitted` 事件（spawn_subagent 只 merge text_delta / subagent_start/end，不 merge 所有 tool_call 事件），不影响生产但 smoke 观测度有限
3. （可选）OCR pipeline 深度集成——当前图片附件走 `FileType.VISUAL` 依赖 tenant 的 PaddleOCR/MinerU 配置；v0.11 可考虑 `doc_archive_attachment` 里同步拉 OCR 并写 markdown 子文档

---

## 最近更新：2026-04-25（Phase 2.7 立项：附件协议 + 下载-验证-归档，设计定版）

**触发**：用户提了两个场景——
1. 对话里直接上传文件，Agent 自动归档到 KB
2. Agent 下载 URL 内容，用户 preview 审核，批准后归档

按用户要求**先深扒 `vendor/claude-code-ref` 同类机制再动手**。Explore 过一遍
`src/utils/attachments.ts` / `bridge/inboundAttachments.ts` /
`EnterPlanModeTool.ts` / `ExitPlanModeV2Tool.ts`，拿到 5 节核心发现：

**关键设计决策（3 条）**：
1. **Attachment 作独立 message type**（不塞 UserMessage 的 content block）
   —— ref `attachments.ts:3675` 的 60+ 子类型证明架构扩展性
2. **不搞通用 pending queue** —— ref 全库 0 命中 `PendingEdit`；我们的
   `submit_plan + plan_gate runtime`（v0.4 做的）就是 ExitPlanMode 等价物
3. **场景 1 + 场景 2 共用** `doc_archive_attachment` 作最后一步；
   `web_fetch_to_attachment` 只材化成 staged attachment，不直接入库

**关键产物**：
- `PLAN-attachments.md`（**新**，完整设计 + ref 引用精确到行号 + 决策 log +
  11 节 11 章，覆盖 DB schema / HTTP 端点 / MCP 工具 / 前端组件 / safety
  limits / 分 Stage 工时拆解 / 拒绝的替代方案 log）
- `CLAUDE.md` / `PLAN.md` 活跃文档清单登记
- `PLAN.md` 新增 Phase 2.7 章节 + v0.9 版本记录

**数据/工具摘要**：
- 新表 `agent_v2_attachment`（session-scoped + 24h TTL + xxhash128 dedupe）
- 新工具 `web_fetch_to_attachment`（sub_librarian）+ `doc_archive_attachment`
  （sub_archivist，plan_gated）
- `submit_plan.preview` 字段扩展（markdown_excerpt，≤ 8KB，前端折叠渲染）
- 前端 composer 文件选择器 + attachment chip + plan card preview 展开

**Stage 分解（11-15h 合计）**：
1. DB + HTTP + ToolContext 附件基础设施（4-5h）
2. `doc_archive_attachment` + `web_fetch_to_attachment` 工具（2-3h）
3. `submit_plan.preview` + 前端 plan card 折叠预览（2h）
4. 前端 composer 附件上传（2-3h）
5. 真机 smoke + 文档更新（1-2h）

**下一步入口**：
1. 用户确认设计（PLAN-attachments.md）→ 开 Stage 1
2. Stage 1 开始：建 migration + `AgentV2AttachmentService` + 3 个 HTTP 端点

---

