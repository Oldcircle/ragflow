# Claude Code 对齐审计 — Phase 2.6 v0.3 整顿

> 基于 `/Users/yb/Opensource/vendor/claude-code-ref/` 的深度读取（`/tmp/architectural-synthesis.md`），
> 对比我们 Agent v2 当前实现，列出所有偏离 Claude Code 惯例的地方 + 优先级 +
> 本轮执行清单。**用户没主动提出、但我应该主动检查的点全部独立标出**。

**产出时间**：2026-04-24 Phase 2.6 v0.2 活体验证之后
**目标**：把 prompt / tool / subagent 三层尽量对齐 Claude Code 设计哲学；
prompts 改为英文；建立后续改进 checklist。

---

## 一、维度 × 对齐度汇总

| # | 维度 | CC 规范 | 我们现状 | 对齐度 | 行动优先级 |
|---|---|---|---|:-:|:-:|
| 1 | Tool 语言 | 全英文 description / prompt | 混中英（`【WHEN】【WHAT】` + 中文说明） | ❌ | **P0** |
| 2 | Tool description vs prompt 分离 | `description()` 短，`prompt()` 长 | 只有 description 一份 | ⚠️ | P2 |
| 3 | `searchHint` 字段 | 每工具都有，3-10 词祈使句 | 完全没有 | ❌ | P1 |
| 4 | `isReadOnly` / `isConcurrencySafe` | 并发调度用 | SDK 未接，但我们 Python 层可记 metadata | ❌ | P1 |
| 5 | `shouldDefer` | 复杂工具默认 defer，靠 ToolSearch 暴露 | 无此机制 | ❌ | P2 |
| 6 | Output schema（Zod + `.describe()`） | 每字段有说明 | 我们 return dict，无 schema | ❌ | P2 |
| 7 | Output 统一 envelope | 错误走 throw，而非 `{"error": ...}` | **我们用 `{"error":"<code>"}`** | 🟡 有意不同 | 保持 |
| 8 | System prompt 8-section 结构 | Intro / System / Tasks / Actions / Tools / Dynamic / User Ctx / System Ctx | 我们单块 free-form | ❌ | **P0** |
| 9 | Dynamic boundary marker | `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` 让静态部分可缓存 | 无 | ❌ | P2 |
| 10 | Prompt 语言 | 英文 | 中文 | ❌ | **P0** |
| 11 | 模型别名（`sonnet/opus/haiku/inherit`） | 每 AgentDef 可声明 tier | `model="inherit"` 已支持；但无 tier 抽象 | 🟡 部分 | P1 |
| 12 | Extended thinking (`thinking` + `max_thinking_tokens`) | `"disabled" \| "adaptive" \| {budgetTokens}` | 未接 | ❌ | P2 |
| 13 | Prompt cache breakpoint discipline | `cacheSafeParams` 父子共享 prefix | 我们用 `history=` 拼 prompt，**不主动管 cache** | ❌ | P2（DeepSeek 无 Anthropic 缓存）|
| 14 | Permission mode 状态机 | default / plan / acceptEdits / bypass / dontAsk / bubble | 我们只有 bypass 单值 | ❌ | P2 |
| 15 | Subagent → parent 的 message stream | `recordSidechainTranscript` 整包留痕 | 仅 `result_preview` 前 4KB | ❌ | P2（存储成本）|
| 16 | Tool `validateInput` / `preparePermissionMatcher` | 预校验层 | 我们靠 `@require_kb_write` 做了类似的事 | 🟢 | 保持 |
| 17 | Tool hook（`preToolUse` / `postToolUse`） | 可插拔 | 只有 audit 是事实上的 postToolUse | 🟡 | P2 |
| 18 | Idempotency | 工具声明 `inputsEquivalent` | 我们有 check_idempotency/remember_result，但不暴露声明 | 🟢 | 保持 |
| 19 | `maxResultSizeChars` 工具级 | 每工具独立 | 全局 32KB，不分 | ❌ | P2 |
| 20 | LRU file state / snippet cache | 每 subagent 独立 cache | 无 | ❌ | 不需要（KB 场景） |

---

## 二、用户提过的点

用户明确要求：
1. ✅ 整体检查并对齐 Claude Code 设计哲学
2. ✅ prompts 写成英文
3. ✅ 检查用户**没提**但应该做的点（下节列）

---

## 三、用户**没提**但我应该标出的问题

### U1. 工具描述过于堆砌，LLM 选择时会迷
- **症状**：17 个工具的 description 都塞了 `【WHEN】【WHAT】【限制】` 三段式，+ 大段 Markdown 列表。Claude Code 的 FileReadTool / BriefTool 描述反而**极简**：描述只占 1-2 句，细节放 `prompt()` 里。
- **影响**：LLM 在工具选择阶段看到的是**所有**工具的 description，17 个每个 300-500 字 → 每次 turn 开头就吃掉 5-8K tokens 的工具菜单，token 浪费 + 关注力分散。
- **修**：description 收到 80 token 以内（一句话 "Use this tool when..."），细节移到 Agent 的 system prompt 里（"When you need X, call rag_retrieve with query=..."）。

### U2. 中文 prompt 本身不是最大问题，**结构化缺失**才是
- **症状**：`strict_rag_prompt` 是 10 行自由格式，`sub_archivist` / `sub_librarian` 的 system_prompt 都是**一大坨**。没有 Role / Context / Constraints / Workflow / Output / Examples 分段。
- **影响**：LLM 每次要自己从 free-form 里提炼该做什么、不该做什么 → 推理出错率上升。Claude Code 的 prompt 虽然长，但**每一段都有标题**（Your strengths / Rules / Output format），LLM 能直接按节索引。
- **修**：用 `SystemPromptBuilder` 把所有 prompt 改成八段式。

### U3. Supervisor 调 spawn_subagent 前**没有思考义务**
- **症状**：A2 活体验证里，supervisor 派了 3 个 subagent，其中 2 个重复（"读笔记规范并创建体检笔记" + "用 create_note 存体检报告"）。LLM 浪费了 ~20% 预算。
- **根因**：supervisor system prompt 没有"决定派谁之前先分析任务 → 按职责派一个 subagent 一次搞定"的规则。
- **修**：supervisor prompt 加一节 "Delegation rules"，明确"一个任务优先派一个 subagent；同一任务重复派 = 浪费，禁止"。

### U4. 缺 "Refusal / Clarification" 决策树
- **症状**：A3 模糊指令 Agent 自己用 Markdown 列选项（没调 ask_user_question）；但换一种风格（"帮我搞定"）Agent 可能直接瞎干。
- **问题**：没有 **何时拒绝 / 何时澄清 / 何时直接做** 的统一规则。Claude Code 的 `CLAUDE.md` / system prompt 都有 "Exploratory questions ... Don't implement until user agrees" 这种明确分流。
- **修**：所有 supervisor prompt 加 "When to clarify vs act" section，给出 3 条 decision rule：
  1. Destructive action with ambiguous target → 必须 ask
  2. Large batch（>5 docs）with clear intent → submit_plan
  3. 单步 + 明确 target → 直接做

### U5. 没做 "tool description 里提到其他工具" 的 cross-ref
- **症状**：`doc_create_note` description 没说"做完后建议调 `kb_stats` 看结果"；`kb_audit` description 没说"如果要写报告就用 `doc_create_note`"。
- **影响**：LLM 不知道工具的**使用链**，每次都要现场推理。
- **修**：每个工具的 prompt 最后加一节 "Related tools and next steps"。

### U6. `submit_plan` 的审批语义是**假的**
- **症状**：`submit_plan` 工具只 emit SSE 事件 + audit，但**不阻塞**后续工具执行。Agent 如果接着就调 `doc_archive`，根本没等用户 approve。
- **根因**：v1 "靠 system prompt 自律" 不足以真约束 LLM。
- **修**（P2 级）：引入会话级 `pending_approval` 状态，写工具调用前检查：若存在 pending plan 且未 resolved → tool 返 `{"error":"awaiting_plan_approval"}`。v1 补个 strict system prompt 警告。

### U7. 没给 Agent 提供 "what happened in previous turn" 的结构化视图
- **症状**：Agent 的 multi-turn history 是**原始 user/assistant 文本**，工具调用细节丢了。如果 Agent 上一轮调了 `kb_audit`，这一轮想"接着分析那个结果"得重新调用。
- **影响**：浪费 token + 降低连续性。Claude Code 的 message history 保留完整 `tool_use` + `tool_result` block。
- **修**（P2）：`AgentV2MessageService.list_for_runner` 把 tool_call_ids 关联的 ToolCall 记录拼回成 tool_use/tool_result 块，喂给下一轮。

### U8. 工具没声明**估算成本 / 时延**
- **症状**：Agent 不知道 `kb_audit` 是 10ms 还是 5s；`doc_upload_from_url` 可能是 30s。
- **影响**：Agent 选择工具时不考虑成本 → 性能规划失败。
- **修**（P2）：metadata 加 `avg_latency_ms` + `cost_class: 'cheap' | 'normal' | 'expensive'`，system prompt 里教 Agent 优先 cheap 工具。

### U9. `AgentDefinition.icon` 是 emoji，**不是 resource ID**
- **症状**：`sub_archivist.icon = "📦"`，`sub_librarian.icon = "📚"`。前端渲染的时候直接显示 emoji——在 Windows / Linux 老浏览器上对齐差。
- **修**（小）：改成 Lucide 图标名 string，前端映射到实际 SVG。

### U10. 工具响应里没有 "suggested next action"
- **症状**：`kb_audit` 返回 `suggestions` 字段，已经是对的方向。但其它工具（tag / rename / archive）响应里不带。
- **影响**：Agent 做完一步不知道下一步该做什么。
- **修**：所有写工具响应加 `next_steps?: string[]`（可选），Agent 能读到提示。

---

## 四、本轮执行清单（P0 + 关键 P1）

| 任务 | 子任务 | 预计文件 |
|---|---|---|
| **T1. Prompt 基础设施**（U2） | `api/agent_v2/prompting/__init__.py` + `builder.py` | 新建 |
| **T2. 工具描述英文化 + 精简**（U1, U5） | 17 个工具 description 改成 ≤80 token 英文；`Related tools` 节挪到 supervisor system prompt | `api/agent_v2/tools/**.py` |
| **T3. System prompt 8-section 重写**（U2, U3, U4） | `strict_rag_prompt` + 6 supervisor + 4 subagent 全改英文 + 结构化 | `api/agent_v2/definitions/built_in/**.py` |
| **T4. 工具元数据注解**（U8, 维度 4/18） | `@tool` wrapper 旁加 `ToolAnnotation: is_read_only, is_idempotent, cost_class` | `tools/base.py` + 全工具 |
| **T5. searchHint 字段**（维度 3） | 每工具一句 3-10 词的 `search_hint` 放 description 首行 | 同 T2 |
| **T6. Delegation rules 内嵌**（U3） | supervisor prompt 明确"一任务一 subagent；禁止重派" | T3 一部分 |
| **T7. Clarify/Act 决策树**（U4） | supervisor prompt 新节 | T3 一部分 |
| **T8. 回归验证** | pytest + live A1/A2/A3 重跑；对比修前修后 Agent 行为 | live test 脚本 |
| **T9. 文档同步** | STATUS + CLAUDE + PLAN | 文档 |

---

## 五、本轮不做、排队给 P1/P2

| 维度 | 原因 |
|---|---|
| description / prompt 分离（U5 的一半） | 需改 SDK 封装；不值当；我们用 Agent system prompt 承担 |
| `shouldDefer` + ToolSearch | DeepSeek 不支持同等机制；暂时不做 |
| Extended thinking | DeepSeek 的 thinking tokens 方式和 Anthropic 不同；后续专门研究 |
| Prompt cache breakpoint | DeepSeek 目前无 Anthropic 风格缓存；Anthropic 模式要再测 |
| Permission mode 状态机 | Claude Code 的 plan/bypass/acceptEdits 对 KB 场景不直接映射；保持 bypass + submit_plan 组合 |
| ContentReplacementState / snippet store | KB 场景没大文件 + 有 chunk 截断；暂不需要 |
| Subagent 全 transcript 落库 | 存储成本；`result_preview` 够诊断 |
| 会话级 `pending_approval` 强约束（U6） | 需要改 AgentV2Session schema；v1 靠 prompt 软约束先观察 |

---

## 六、执行完 T1–T9 后的预期改变

| 指标 | 当前 | 预期 |
|---|---|---|
| 工具 description 总 token（17 工具） | ~5-8K | ~1-1.5K |
| System prompt 结构化节数 / supervisor | 1 块 free-form | 7 段 |
| Prompt 语言 | 中文 | 英文 |
| A2 场景里 supervisor 派的 subagent 数 | 3（有重复） | 1-2 |
| Agent 选工具正确率（主观） | ~80% | ~90%+ |

---

## 七、成功标准

1. **pytest 不挂** — 198 passed, 8 skipped 保持
2. **Ruff 不挂**
3. **A1 活体**：supervisor 用 `kb_stats + rag_list_docs` 答 + 不 call kb_audit
4. **A2 活体**：supervisor 派 **恰好 1** 个 sub_librarian（不再 ×3）；cleanup 删 1 doc（create_note 真调了）
5. **A3 活体**：Agent 明确识别歧义 → 优先选 `ask_user_question` 工具（允许退回 Markdown）
6. **单次 turn token 消耗**：A1 / A3 下降 ≥ 25%（tool menu 瘦身 + system prompt 清晰）

---

## 八、版本记录

| 日期 | 版本 | 决策 |
|---|---|---|
| 2026-04-24 | v0.1 | 基于 `/tmp/architectural-synthesis.md` 的 Claude Code 深读，列出全部 20 个维度 + 10 个用户没提的点，定 P0 范围 |
