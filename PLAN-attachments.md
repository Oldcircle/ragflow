# Phase 2.7 — Attachments & Download-Verify-Archive

> **目标**：让 Agent 对话里能**直接接入文件**（用户上传 / Agent 下载），经**用户 preview 确认**后归档到 KB。
>
> **关键洞见**（from `claude-code-ref` 深度调研）：Claude Code **不用** pending queue，而是组合两个原语：**Permission Mode**（"这段时间你只能读"）+ **Attachment Message**（独立 message type 传状态）。我们的 `submit_plan` + plan gate runtime（Phase 2.6 v0.4）已经是 Plan Mode 的等价物——只需补 **attachment 协议层** + **preview payload**。

---

## 一、核心设计决策（3 条）

### 1. 附件用**独立 message type**，不塞进 content block

对齐 `claude-code-ref/src/utils/attachments.ts:3675`：
```typescript
createAttachmentMessage(attachment) {
  return { attachment, type: 'attachment', uuid, timestamp }
}
```

我们的 Python 映射：每个附件在 DB 建独立 `AgentV2Attachment` 行；runtime 通过 `ToolContext.attachments: tuple[AttachmentInfo, ...]` 传给 supervisor 系统提示；前端消息气泡旁独立渲染附件 chip。

**为什么不塞 content block**：
- 附件元信息（filename / mime / size / status）跟消息正文生命周期**不同**——附件在归档后会变 `archived_doc_id`，而用户消息文本不会变
- Claude Code 60+ 个 attachment 子类型（file / directory / plan_mode / agent_listing 等）都独立 message 存在，证明这个抽象足够扛后续扩展

### 2. 不搞通用 pending queue，**plan gate 就是 staging 机制**

ref 全库搜 `PendingEdit / PendingWrite / confirmDiff` → **0 命中**。claude-code-ref 的惯例是：
- **Read-only 阶段**：permission_mode='plan' 硬性禁写（`EnterPlanModeTool.ts:118` "DO NOT write or edit any files yet"）
- **确认阶段**：ExitPlanMode 把 plan 文本给用户看 + 批准
- **执行阶段**：permission 恢复，Agent 正常调写工具

**我们的映射**：
- `submit_plan` = ExitPlanMode（我们已经有了）
- plan gate runtime = permission_mode='plan' 的执行层（我们已经有了）
- `get_pending_plan` = 批准后读回 plan 内容（我们已经有了）
- **缺的**：`submit_plan` 的 `preview` 字段（展示**具体内容** 给用户审批，不只是步骤元信息）

### 3. 照抄 ref 的 size limits，业务限制更严

ref `src/constants/apiLimits.ts` 的关键常量：

| 常量 | ref 值 | 我们的值 | 理由 |
|---|---:|---:|---|
| 单文件 base64 上限 | 5 MB (image) / 20 MB (PDF) | **50 MB** | KB 入库场景允许更大（已在 doc_upload_from_url 用过）|
| 单 request media 数 | 100 | **5** | 避免 Agent 被滥用当批量上传器 |
| 单 session 附件累计 | 无显式 | **200 MB / 20 个** | 防未归档附件堆积吃存储 |
| PDF 页数 | 100 页 | **500 页** | KB 场景常见大政策 |
| Preview text 大小 | N/A | **8 KB** | Plan card UI 展示上限 |

**MIME 白名单**（v0.9 决议更新后）：`pdf / docx / doc / xlsx / xls / pptx / ppt / md / txt / csv / json / html` + **图片** `jpg / jpeg / png / webp / gif`（走 OCR 路径，见本节下）。

### 图片支持：走 OCR 不走 Vision

**决策依据**（深扒 `claude-code-ref` + 上游 RAGFlow 得出）：

| 路径 | 做法 | 适用场景 |
|---|---|---|
| 🔴 不用 | **Vision inline** — 图片 base64 送 LLM 看像素 | Claude Code 用这个（代码 agent 实时看截图）|
| 🟢 用 | **OCR 抽文本** — PaddleOCR / MinerU → markdown → 入库可检索 | 我们的 KB 归档场景 |

**理由**：KB 归档 = **持久可检索存储**。Vision 路径每次都要模型看像素（成本高 + 不可 rag_retrieve）；OCR 路径一次抽文本持久化，后续所有查询都走 `rag_retrieve` 廉价。

**RAGFlow 侧现状**：
- `rag/llm/ocr_model.py` 已集成 **PaddleOCR**（轻量离线）+ **MinerU**（高精度复杂布局）
- `deepdoc/vision/` 有完整 OCR pipeline
- 但 `FileService.upload_document` 对直接上传的 `.jpg/.png` **只生成缩略图**、**不 OCR 入库**（上游只对 PDF 内嵌图片 OCR）—— 我们要补这条路径

**实施策略**（放在 Stage 2 `doc_ingest_attachment` 里）：
1. 上传阶段（Stage 1）：图片正常存 MinIO，status=staged，**不做 OCR**
2. Preview 阶段（Stage 1）：图片的 `preview_text` 字段先存"[image, OCR pending]"
3. Archive 阶段（Stage 2 `doc_ingest_attachment`）：
   - 检测 `mime_type.startswith("image/")` 时分流
   - 调 `PaddleOCRParser` 抽文本（同步，通常 <3s）
   - 创建 **markdown Document**（内容 = OCR 文本 + 顶部元信息 "[source: 原始文件名.jpg, hash=xxx]"）
   - 把原始图片的 MinIO path 存到 Document.meta_fields.source_blob（审计 / 日后重跑 OCR 用）
   - status → archived
4. Tenant 配置：`ocr_engine = paddleocr (默认) | mineru | vision_llm`（后两者按需）

**Size / Count 上限**：图片沿用单文件 50 MB 上限；但**建议用户压缩后再传**（提示"大于 5 MB 可能 OCR 慢"）。

**工作量**：在 Stage 2 加 OCR 分支 **+2h**；Stage 5 smoke 加图片用例 **+0.5h**。总工时从 11-15h → **13.5-17.5h**。

---

## 二、参考 claude-code-ref 的精确发现

| 问题 | ref 答案 | 文件:行 |
|---|---|---|
| Attachment 数据结构 | 独立 message，`type:'attachment'` + uuid + timestamp + 嵌套 attachment 对象 | `attachments.ts:3675-3690` |
| 附件进 prompt 的路径 | UserMessage 文本前缀 `@"/path/to/file"`，由 Read tool 负责读 | `inboundAttachments.ts` |
| 图片上传落盘 | base64 → `~/.claude/paste-cache/{timestamp}.png` | `imageStore.ts` |
| Web 上传桥 | web composer POST `file_uuid + file_name` → 下载到 `~/.claude/uploads/{sessionId}/` → 注入 @path | `bridge/inboundAttachments.ts` |
| Plan mode 二阶段 | EnterPlanMode 硬禁写 → 写 plan 到 `~/.claude/plans/{sessionId}.md` → ExitPlanMode 展示 → user approve | `EnterPlanModeTool.ts:77-118` + `ExitPlanModeV2Tool.ts:147-226` |
| Plan 内容字段 | `plan: string` + `planWasEdited: bool` + `awaitingLeaderApproval: bool` | `ExitPlanModeV2Tool.ts:110-141` |
| 去重机制 | `filterDuplicateMemoryAttachments()` 查 FileStateCache hash | `attachments.ts:3340` |
| Reminder 周期注入 | 每 5 turn 注入 PLAN_MODE_ATTACHMENT_CONFIG | `attachments.ts:259-267` |

**未找到**（ref 没做，我们也不做）：
- 病毒扫描
- 通用 pending queue 抽象
- 单 attachment 独立 cache_control

---

## 三、两场景的架构

### 场景 1：用户上传 → Agent 自动归档

```
[User]                [Frontend]              [Backend]          [Agent Runner]
  │ drag file            │                       │                     │
  ├─────────────────────>│                       │                     │
  │                      │ POST /session/<id>/   │                     │
  │                      │  attachments (multi)  │                     │
  │                      ├──────────────────────>│                     │
  │                      │                       │ save MinIO          │
  │                      │                       │ hash + dedupe       │
  │                      │                       │ insert attachment   │
  │                      │                       │   row (status:staged│
  │                      │ {attachment_id,       │)                    │
  │                      │  preview_text}        │                     │
  │                      │<──────────────────────┤                     │
  │                      │ show chip             │                     │
  │ types "归档这份"     │                       │                     │
  ├─────────────────────>│                       │                     │
  │                      │ POST /conversation    │                     │
  │                      │  + attachment_ids     │                     │
  │                      ├──────────────────────>│                     │
  │                      │                       │ ctx.attachments set │
  │                      │                       ├────────────────────>│
  │                      │                       │                     │
  │                      │                       │        supervisor sees prompt:
  │                      │                       │        "# Pending attachments"
  │                      │                       │                     │
  │                      │                       │    spawn sub_archivist
  │                      │                       │                     │
  │                      │                       │    submit_plan(
  │                      │                       │      preview=attachment.preview_text[:2K],
  │                      │                       │      affected=[att, kb]
  │                      │                       │    )
  │                      │ SSE plan_submitted    │                     │
  │                      │ + preview card        │                     │
  │                      │<──────────────────────┤<────────────────────┤
  │ sees preview +       │                       │                     │
  │ clicks approve       │                       │                     │
  ├─────────────────────>│ "[plan approved]"     │                     │
  │                      ├──────────────────────>│                     │
  │                      │                       │ plan_gated unlock   │
  │                      │                       ├────────────────────>│
  │                      │                       │   doc_ingest_attachment(
  │                      │                       │     attachment_id, kb_id)
  │                      │                       │   → FileService.upload_document
  │                      │                       │   → status=archived
  │                      │                       │   → return {doc_id, next_steps}
  │                      │ SSE final text        │                     │
  │                      │<──────────────────────┤<────────────────────┤
```

### 场景 2：Agent 下载 → 用户 preview → 归档

```
[User]                        [Agent]                       [KB/Web]
  │ "把 https://... 入 KB"      │                                │
  ├────────────────────────────>│                                │
  │                             │ web_fetch_to_attachment(url)   │
  │                             ├───────────────────────────────>│
  │                             │ ← temp attachment row          │
  │                             │   {id, preview_text,           │
  │                             │    source_url, hash}           │
  │                             │                                │
  │                             │ (optional) rag_retrieve —      │
  │                             │ check if同 URL 已有 doc in KB  │
  │                             │                                │
  │                             │ submit_plan(                   │
  │                             │   preview=excerpt(content),    │
  │                             │   steps=[                      │
  │                             │     "从 <url> 下载 (已完成)",  │
  │                             │     "入库到 <target_kb>"],     │
  │                             │   affected=[{url}, {kb}])      │
  │ sees preview card + approve │                                │
  │<────────────────────────────┤                                │
  │ "[plan approved]"           │                                │
  ├────────────────────────────>│                                │
  │                             │ get_pending_plan               │
  │                             │ doc_ingest_attachment(        │
  │                             │   att_id, kb_id) →             │
  │                             │ 复用 scenario 1 入库路径       │
```

**关键**：场景 2 **不需要**新入库工具——`web_fetch_to_attachment` 材化成 attachment 后，**和场景 1 共用** `doc_ingest_attachment` 入库。一套代码两个场景。

---

## 四、数据模型

### 新表 `agent_v2_attachment`

```sql
CREATE TABLE agent_v2_attachment (
  id              VARCHAR(36)  PRIMARY KEY,
  session_id      VARCHAR(36)  NOT NULL,  -- FK agent_v2_session
  tenant_id       VARCHAR(36)  NOT NULL,
  uploaded_by     VARCHAR(36)  NOT NULL,  -- user_id

  filename        VARCHAR(255) NOT NULL,
  mime_type       VARCHAR(100) NOT NULL,
  size_bytes      BIGINT       NOT NULL,
  hash_xxh128     VARCHAR(32)  NOT NULL,  -- dedupe key
  blob_path       VARCHAR(500) NOT NULL,  -- MinIO object path

  origin          ENUM('upload', 'web_fetch', 'agent_generated') NOT NULL,
  source_url      VARCHAR(2048) NULL,      -- only for web_fetch
  preview_text    TEXT          NULL,      -- first ~8KB plain text

  status          ENUM('staged', 'archiving', 'archived', 'rejected', 'expired') NOT NULL,
  archived_doc_id VARCHAR(36)   NULL,      -- FK document after archive
  archived_kb_id  VARCHAR(36)   NULL,
  archived_at     TIMESTAMP     NULL,

  created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),
  expires_at      TIMESTAMP     NULL,      -- 24h TTL for staged

  INDEX idx_session (session_id, status),
  INDEX idx_hash (tenant_id, hash_xxh128, status),  -- dedupe
  INDEX idx_expires (expires_at)                    -- cleanup cron
);
```

**TTL 策略**：
- `staged` 默认 24h 后 `expires`；expired 行定期清 MinIO blob + DB row
- `archived` 永久保留（作为审计链）
- `rejected` 立即清 blob，row 保留 7 天审计

### `AgentV2Session` 不改，通过外键关联

### `ToolContext` 扩展

```python
@dataclass
class ToolContext:
    # ... existing fields ...
    # Phase 2.7
    attachments: tuple[AttachmentInfo, ...] = ()
    """Staged/archived attachments on this session (read-only snapshot)."""


@dataclass(frozen=True)
class AttachmentInfo:
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    origin: str  # 'upload' | 'web_fetch' | 'agent_generated'
    status: str  # 'staged' | 'archived' | 'rejected'
    preview_text: str | None  # first 8KB
    source_url: str | None
    archived_doc_id: str | None
```

---

## 五、新增的端点 + 工具

### HTTP 端点

| Method | Path | 说明 |
|---|---|---|
| POST | `/v1/agent_v2/session/<id>/attachments` | multipart file upload；返 `{attachment_id, filename, preview_text}` |
| GET | `/v1/agent_v2/session/<id>/attachments` | 列当前 session 的 staged + archived 附件 |
| DELETE | `/v1/agent_v2/session/<id>/attachments/<att_id>` | 用户撤销（status → rejected） |

### MCP 工具（2 个新 + 1 个扩展）

#### 新 `web_fetch_to_attachment`（sub_librarian 可用）

```python
@tool(
    name="web_fetch_to_attachment",
    description=(
        "Use this tool when the user asks you to archive a specific web URL "
        "into the knowledge base. It downloads the page, stores it as a staged "
        "attachment on the current session, and returns a preview — but does "
        "NOT archive to the KB yet. The user must approve via submit_plan "
        "before calling doc_ingest_attachment.\n\n"
        "Usage notes:\n"
        "- Public HTTP(S) only; SSRF-protected (same three-stage defense as "
        "web_fetch).\n"
        "- 50 MB max; HTML/PDF/DOC/markdown accepted.\n"
        "- Returns preview_text (first 8 KB) for user review.\n"
        "- DEDUP: if same URL + tenant already staged or archived, returns "
        "existing attachment_id.\n"
        "- Do NOT use this for quick lookups (use web_fetch). Use this only "
        "when the end goal is to ingest into KB."
    ),
    ...
)
```

#### 新 `doc_ingest_attachment`（sub_archivist 专用，plan_gated）

```python
@tool(
    name="doc_ingest_attachment",
    description=(
        "Use this tool to archive a staged session attachment into a "
        "knowledge base. The attachment must be in status='staged' and the "
        "caller must have CONTRIBUTOR access on the target KB.\n\n"
        "Under the hood this calls FileService.upload_document (same pipeline "
        "as the dataset UI upload), runs through parse queue, and marks the "
        "attachment status='archived'.\n\n"
        "Usage notes:\n"
        "- plan_gated: requires submit_plan approval before first write in "
        "this turn.\n"
        "- IDEMPOTENT: archiving the same attachment twice is a noop "
        "(returns existing doc_id).\n"
        "- Optional doc_name override; defaults to attachment.filename.\n"
        "- Optional tags array applied after ingest."
    ),
    ...
)
```

#### 扩展 `submit_plan` — 新 `preview` 字段

```python
# 新增字段（向后兼容，旧 caller 仍可 omit）
{
    "preview": {
        "kind": "markdown_excerpt",        # or "diff" / "url_dump"
        "title": "深圳保障房 2026 新规",
        "excerpt": "<最多 8KB markdown>",
        "source_ref": "https://...",        # URL / attachment_id / doc_id
        "truncated": true                    # is this excerpt partial?
    }
}
```

### 前端组件改动

- `composer.tsx` 加文件按钮（`<input type="file" multiple>` + drag-drop zone）
- 新 chip：`components/attachment-chip.tsx` 展示 filename + size + status + 撤销 X
- `pending-plan-card.tsx` 扩展：若 plan 含 `preview.kind === 'markdown_excerpt'`，加**可折叠 preview 区**（max 400px 高，scroll，markdown 渲染）

---

## 六、分阶段实施

严格对标 ref 做法、最小前向依赖：

### Stage 1 — 附件基础设施（后端）

**工时**：4-5h

- Migration：`agent_v2_attachment` 表
- `AgentV2AttachmentService`：`create / list / mark_archived / reject / expire_stale`
- MinIO blob path：`agent_v2/{tenant}/{session}/{attachment_id}`
- Preview 抽取：pdf/docx/txt 首 8KB plain text（复用 `deepdoc/parser/` 已有 parser，但只取前 N 页）
- SSRF / size 限制沿用 `doc_upload_from_url` helpers
- 3 个 HTTP 端点 + `@login_required` + `@validate_request`
- `ToolContext.attachments` 注入（在 `agent_v2_app.py::send_message` 里读 session's staged attachments 然后设到 ctx）
- Supervisor system prompt 加段：`# Session attachments (pending user decision)` 列当前 staged 附件

**测试**：
- `test_agent_v2_attachment_service.py` — CRUD + dedupe + expire
- `test_attachment_api.py` — 上传 / 列表 / 撤销；size / mime 拒绝路径
- `test_tool_context_attachments.py` — ctx.attachments 正确填充

### Stage 2 — Archive 工具（sub_archivist 增强）

**工时**：2-3h

- `doc_ingest_attachment.py` — 走 `@require_kb_write(plan_gated=True)`
- `web_fetch_to_attachment.py` — 复用 `web_fetch` 的 SSRF / httpx / markdownify
- 注册到 `ALL_TOOLS` + annotations + prompting hints
- `sub_archivist` definition 加 `doc_ingest_attachment`；`sub_librarian` definition 加 `web_fetch_to_attachment`
- `sub_archivist` system prompt 加"检查 ctx.attachments 是否有 staged 的 → 必要时 submit_plan 归档"工作流

**测试**：
- `test_doc_ingest_attachment.py` — 入库成功 / 已归档返 existing / plan gate 拒 / RBAC 拒
- `test_web_fetch_to_attachment.py` — SSRF / 拒绝非 HTTP / dedup on same URL / preview 生成

### Stage 3 — submit_plan preview 字段 + 前端卡片

**工时**：2h

- `submit_plan.py` input_schema 加可选 `preview` 字段
- 后端 plan 持久化保留 preview 到 `AgentV2Session.pending_plan_metadata_json`
- `get_pending_plan` 响应带 preview
- 前端 `pending-plan-card.tsx` 加折叠 preview 区（react-markdown 渲染，max-height 400px + overflow auto）
- i18n：zh/en 各加 4 个 key

**测试**：
- `test_submit_plan_preview.py` — 带 preview / 不带 preview 的向后兼容
- 前端 storybook 新 story：plan card with preview

### Stage 4 — 前端 composer 附件上传

**工时**：2-3h

- `composer.tsx` 按钮 + `<input type=file>` + drag-drop zone
- `useAttachmentUpload` hook — 上传到 `POST /session/<id>/attachments`，progress，取消
- 消息上方/输入框内展示"待发送附件 chips"
- 发送时把 `attachment_ids` 绑到 user message
- 消息气泡上沿展示已发送的附件 chip（点击展示 preview 摘要）

**测试**：
- `test_composer_attachment.test.tsx`（jest）— 文件选择 → 上传 → 绑定到 message
- 手工 E2E：上传 PDF → Agent 回应"我看到一份 XX 文件，你希望归档到哪个 KB？"

### Stage 5 — 验收 smoke + 文档

**工时**：1-2h

- `scripts/smoke_attachment_archive.py` — 场景 1 全链路（create session → upload → send message asking to archive → verify attachment archived → doc appears in KB）
- `scripts/smoke_web_download_archive.py` — 场景 2（ask Agent to archive https://xxx → verify submit_plan with preview → approve → verify archived）
- 更新 `TEST-MANUAL-v0.4.md` → `TEST-MANUAL-v0.8.md` 加 G13 附件组用例
- 更新 `FORK.md` 版本表 + `STATUS.md` 新 phase

**工时合计**：**11-15h**

---

## 七、Safety limits 汇总

### 单文件
- Size: **50 MB**（upload + web_fetch 通用）
- MIME 白名单：`pdf / doc / docx / xls / xlsx / ppt / pptx / md / txt / csv / json / html`
- Preview 抽取：首 **8 KB** plain text（Plan card 展示）

### 单 session
- Staged 附件上限：**20 个 / 200 MB**（超出拒上传）
- Staged TTL：**24 h** 自动 expire + 清 MinIO

### 单 turn
- 不限附件**引用**数（从 ctx 读），但 `submit_plan` 单次 `affected_resources` ≤ 20

### Dedup
- `hash_xxh128` per tenant — 同内容多次上传返同一 attachment_id
- Web URL dedup per tenant — 同 URL 24h 内复用

### RBAC
- 上传附件：session.owner 或 ADMIN
- Archive：target KB CONTRIBUTOR+
- 删除 / reject：attachment.uploaded_by 或 session.owner

### 审计
- 新 action：`agent_v2.attachment_upload` / `agent_v2.attachment_reject` / `kb.doc.archive_attachment`
- 日常 cron 记 `agent_v2.attachment_expire`

---

## 八、为什么这样设计（拒绝的替代方案）

### 拒绝 A：把附件塞进 UserMessage 的 content block
- content block 生命周期跟消息绑死；附件归档状态变化会改消息，不合适
- Claude Code 60+ attachment 类型都独立 message 存在——架构赢

### 拒绝 B：维护通用 `PendingEdit` queue 抽象
- ref 全库 0 命中；维护状态机成本高
- 我们已有 `submit_plan + plan_gate` 的 permission-mode 等价物，复用更简

### 拒绝 C：让 Agent 自己决定何时入库（不走 plan_gate）
- 附件归档是**持久写入 KB**，用户必须有审批环节
- 除非用户显式 "已启用自动归档"（未来 feature，不在本 phase）

### 拒绝 D：附件绑定到 user_id 不是 session_id
- 跨 session 泄露风险（其他 session 可能引用到）
- session scope 更清晰 + 便于 TTL 清理

---

## 九、未来扩展（不在本 phase）

- `attachment_type = "image"` + OCR → 接 deepdoc OCR pipeline
- 自动归档白名单（受信 URL 不走 plan gate）
- 附件到附件的转换（PDF 拆章节 → N 个 markdown 附件）
- 跨 session 附件复用（已有 tenant 级 dedup，只需 UI 暴露）
- `doc_ingest_attachment_batch` 批量归档
- Agent 自主上传（从 spawn_subagent 递归场景）

---

## 十、活跃文档清单更新

本 phase 启动后：
- **新**：`PLAN-attachments.md`（本文档）
- **改**：`PLAN.md` Phase 2.7 章节
- **改**：`STATUS.md` 新增 Phase 2.7 快照
- **改**：`CLAUDE.md` 活跃文档清单加本文档
- **改**（完成后）：`FORK.md` 版本表

---

## 十一、决策 log

| 日期 | 决策 | 依据 |
|---|---|---|
| 2026-04-25 | 对齐 claude-code-ref attachment-as-message pattern，不用 content block | ref `attachments.ts:3675`；60+ 子类型已验证扩展性 |
| 2026-04-25 | 复用 `submit_plan + plan_gate` 作 staging 机制，不另起 PendingQueue | ref 全库无 PendingEdit 抽象；我们的 plan gate 是 ExitPlanMode 等价物 |
| 2026-04-25 | 场景 1 + 场景 2 共用 `doc_ingest_attachment`；`web_fetch_to_attachment` 只管 materialize | 两场景最后一步都是"把 staged attachment 入 KB"，没必要两条路径 |
| 2026-04-25 | 50 MB 单文件 / 20 个 session / 24h TTL | 与 `doc_upload_from_url` 对齐；ref API_MAX_MEDIA_PER_REQUEST=100 不适合 KB 场景 |
