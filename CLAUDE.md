# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RAGFlow is an open-source RAG (Retrieval-Augmented Generation) engine based on deep document understanding. It's a full-stack application with:

- Python backend (Flask-based API server)
- React/TypeScript frontend (built with vitejs)
- Microservices architecture with Docker deployment
- Multiple data stores (MySQL, Elasticsearch/Infinity, Redis, MinIO)

## Architecture

### Backend (`/api/`)

- **Main Server**: `api/ragflow_server.py` - Flask application entry point
- **Apps**: Modular Flask blueprints in `api/apps/` for different functionalities:
  - `kb_app.py` - Knowledge base management
  - `dialog_app.py` - Chat/conversation handling
  - `document_app.py` - Document processing
  - `canvas_app.py` - Agent workflow canvas
  - `file_app.py` - File upload/management
- **Services**: Business logic in `api/db/services/`
- **Models**: Database models in `api/db/db_models.py`

### Core Processing (`/rag/`)

- **Document Processing**: `deepdoc/` - PDF parsing, OCR, layout analysis
- **LLM Integration**: `rag/llm/` - Model abstractions for chat, embedding, reranking
- **RAG Pipeline**: `rag/flow/` - Chunking, parsing, tokenization
- **Graph RAG**: `rag/graphrag/` - Knowledge graph construction and querying

### Agent System (`/agent/`) — 上游原版 Dialog 画布模式

- **Components**: Modular workflow components (LLM, retrieval, categorize, etc.)
- **Templates**: Pre-built agent workflows in `agent/templates/`
- **Tools**: External API integrations (Tavily, Wikipedia, SQL execution, etc.)

### Agent v2（本 fork 的核心差异化，`/api/agent_v2/`）
- **Runner**: `api/agent_v2/runner.py` — Claude Agent SDK 适配层；P2.5.2 起支持 `history=` / `summary_text=` 多轮参数，P2.5.bugfix 起开 `include_partial_messages=True` 真流式，Phase 2.6 v0.4 起接 `pending_plan_status/id` 并注入 `ToolContext` 供 gate 使用；v0.5 加 `_format_tool_calls_for_history` 把 tool 往返渲染成 `[tool] name(args) → result` 紧凑行插到 `<conversation-history>`，追问"刚才那个 kb_audit 结果"时不用重跑
- **Event**: `api/agent_v2/event.py` — 统一 SSE 事件；P2.3 / P2.5.1 加了 `subagent_*` / `citation_warning`；Phase 2.6 加了 `ask_user_question` / `plan_submitted`
- **Tools**: `api/agent_v2/tools/` — 22 个 MCP 工具：4 读（rag_retrieve / rag_list_docs / rag_read_doc / rag_graph_query）+ spawn_subagent + 7 写（doc_tag / doc_rename / doc_archive / doc_reparse / doc_upload_from_url / kb_create / **doc_create_note**）+ 3 自省（kb_audit / kb_stats / doc_list_recent_changes）+ 3 交互 / plan（ask_user_question / submit_plan / get_pending_plan）+ 2 公网（Phase 2.6 v0.7：web_search / web_fetch）+ 2 附件（Phase 2.7 Stage 2：web_fetch_to_attachment / doc_ingest_attachment）。Phase 2.8.2 起 `spawn_subagent` 描述里自动注入 `Available subagent types and the tools they have access to:` 段（参照 `claude-code-ref/packages/builtin-tools/src/tools/AgentTool/prompt.ts::formatAgentLine`），supervisor 不再幻觉子代理工具集
- **Annotations** (Phase 2.6 v0.4 / v0.5): `api/agent_v2/annotations.py` — 每个工具的 `ToolAnnotation(is_read_only, is_idempotent, cost_class, avg_latency_ms, side_effects)`；v0.4 通过 `annotations_summary_for_prompt` 渲染进 supervisor / subagent system prompt 的 **Tool cost hints** 段；v0.5 `registry._decorate_for_mcp` 还把它投影成 MCP 协议原生的 `readOnly/destructive/openWorld` annotations，并把 `SEARCH_HINT_BY_TOOL` 里的 3-10 词祈使句贴到 MCP description 首行作 `[intent]` 前缀
- **Interactive Tool Pause Framework** (Phase 2.6 v0.4 + Phase 2.8.3): 两个交互工具（`submit_plan` / `ask_user_question`）共用一套 cancel + resume 框架，**程序硬阻塞 SDK loop**——不靠 prompt 嘱托模型 STOP（软约束）。参照 claude-code-ref `AskUserQuestionTool.tsx` 的 `shouldDefer=true` + `checkPermissions: 'ask'` 设计，落地为 HTTP-SSE 约束下的等价：
   - **持久化层**：`AgentV2Session` 表两组平行列 `pending_{plan,question}_{id,status,submitted_at,body}` + `AgentV2SessionService.{set,transition,clear,get}_pending_{plan,question}` 服务方法
   - **工具层**：通过 `tools/base.py::mcp_pause_response()` 在响应顶层设 `pause_loop=true` 标记
   - **runner 层**：`runner._has_pause_loop_marker()` + `tool_call_end` 早期短路，emit end 后 return；模型根本没机会消费 tool_result 继续生成
   - **resume 层**：`api/agent_v2/plan_decision.py` 的 `parse_plan_decision`（`[plan approved\|rejected\|request changes]`）+ `parse_question_answer`（`[answer: <label>]`），剥前缀 + `augment_for_*` 注入 `[plan system]` / `[question system]` directive 让 supervisor 知道这是 resume 信号，而不是 out-of-domain 请求
   - **plan_gate 兜底**（与 pause 互补，针对 supervisor 试图直接调写工具的极端情况）：`@require_kb_write` 的 `plan_gated=True`（默认开）两层 gate（`ctx.plan_submitted_this_turn` + DB live read），1h TTL；只有 `doc_create_note` 以 `plan_gated=False` 跳过
- **Validators** (P2.5.1 + Phase 2.8.2): `api/agent_v2/validators/` — EvidenceIndex + CitationValidator（4 规则：**citation_without_evidence**（短路前置） / missing_chunk / number_unsupported / no_citation_for_numeric）+ `rewrite.py` 双 rewrite 路径（`rewrite_answer_strict` 修引用 ↔ `rewrite_to_no_basis` 无证据时降级为免责声明）。Phase 2.8.2 prompt 设计参照 `claude-code-ref/packages/builtin-tools/src/tools/AgentTool/built-in/verificationAgent.ts` 的对抗式风格 + 命名 LLM 真实会用的"理性化跳过"借口逐条反驳。命中 phantom 时 `runner._audit_citation_phantom` 写 `access_audit_log(action='agent_v2.citation_phantom')` 便于 dashboard 聚合
- **Compactor** (P2.5.2): `api/agent_v2/compactor.py` — 历史摘要压缩；`run_compact_safely` 入口把任何异常写进 access_audit_log（action=`agent_v2.compact`），fire-and-forget 不再静默失败
- **Definitions** (P2.5.3 + P2.6 v0.2 / v0.4): `api/agent_v2/definitions/` — 声明式 AgentDefinition schema + 6 supervisor + 4 subagent（sub_policy_researcher / sub_evidence_checker / sub_archivist / sub_librarian）。**sub_archivist = 动手改**（v0.4 升 v1.2.0，hard rules 点名 runtime gate），**sub_librarian = 看+想+写笔记**，Claude Code 风格一 subagent = 一心智模式
- **doc_ops** (Phase 2.6 + v0.2 + v0.4): `api/agent_v2/tools/doc_ops/` — 10 个工具共享 `@require_kb_write` 装饰器 + `ok/err` 响应形状：
  - 6 写（tag / rename / archive / reparse / upload_from_url / kb_create）→ `sub_archivist` 专用；v0.4 成功路径加 `next_steps` 提示
  - 4 自省 / 总结（create_note / kb_audit / kb_stats / list_recent_changes）→ `sub_librarian` 专用
  - supervisor 默认都拿不到写或运营工具；必须通过 `spawn_subagent(subagent_type=...)` 显式派 archivist 或 librarian
- **Bot Channels** (P2.2): `api/bot_channels/` — 飞书 webhook adapter（签名 + 会话映射）
- **HTTP App**: `api/apps/agent_v2_app.py` — 注册 `/v1/agent_v2/*` blueprint；v0.4 起在 `send_message` 入口解析 plan 前缀 / 落 DB / 注入 runner ctx / 在 turn 收尾清 stale 状态

### Frontend (`/web/`)

- React/TypeScript with vitejs framework
- shadcn/ui components
- State management with Zustand
- Tailwind CSS for styling

## Common Development Commands

### Backend Development

```bash
# Install Python dependencies
uv sync --python 3.12 --all-extras
uv run download_deps.py
pre-commit install

# Start dependent services
docker compose -f docker/docker-compose-base.yml up -d

# Run backend (requires services to be running)
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh

# Run tests
uv run pytest

# Linting
ruff check
ruff format
```

### Frontend Development

```bash
cd web
npm install
npm run dev        # Development server
npm run build      # Production build
npm run lint       # ESLint
npm run test       # Jest tests
```

### Docker Development

```bash
# Full stack with Docker
cd docker
docker compose -f docker-compose.yml up -d

# Check server status
docker logs -f ragflow-server

# Rebuild images
docker build --platform linux/amd64 -f Dockerfile -t infiniflow/ragflow:nightly .
```

## Key Configuration Files

- `docker/.env` - Environment variables for Docker deployment
- `docker/service_conf.yaml.template` - Backend service configuration
- `pyproject.toml` - Python dependencies and project configuration
- `web/package.json` - Frontend dependencies and scripts

## Testing

- **Python**: pytest with markers (p1/p2/p3 priority levels)
- **Frontend**: Jest with React Testing Library
- **API Tests**: HTTP API and SDK tests in `test/` and `sdk/python/test/`

## Database Engines

RAGFlow supports switching between Elasticsearch (default) and Infinity:

- Set `DOC_ENGINE=infinity` in `docker/.env` to use Infinity
- Requires container restart: `docker compose down -v && docker compose up -d`

## Development Environment Requirements

- Python 3.10-3.12
- Node.js >=18.20.4
- Docker & Docker Compose
- uv package manager
- 16GB+ RAM, 50GB+ disk space

---

## Fork 信息

- **上游**: `infiniflow/ragflow`
- **Fork**: `Oldcircle/ragflow` (https://github.com/Oldcircle/ragflow)
- **本地路径**: `~/Opensource/forks/ragflow`
- **remotes**: `origin` → 本人 fork；`upstream` → infiniflow/ragflow
- 同步上游：`git fetch upstream && git merge upstream/main`

## 端口

依赖服务（docker-compose-base.yml）：
- MySQL 8.0.39 → `3306`
- Elasticsearch 8.11.3 → `1200`
- MinIO API → `9000`，Console → `9001`
- Redis (Valkey 8) → `6379`

应用：
- 后端 Flask/Quart API → `9380`（`/v1/**` 路由，未登录返 401）
- 前端 Vite dev server → `9222`（package.json 实际端口；AGENTS.md 文档误写 8000）

默认密码（本地开发）：
- MySQL root / infini_rag_flow
- ES elastic / infini_rag_flow
- MinIO rag_flow / infini_rag_flow
- Redis 无用户名 / infini_rag_flow

安全相关环境变量：
- `RAGFLOW_BOT_CHANNEL_SECRET_KEY` — BotChannelService 加密飞书
  `app_secret` / `encrypt_key` / `verification_token` 的优先密钥。生产环境必须配置
  稳定值；未配置时回退 `settings.SECRET_KEY`，仅适合本地开发/灰度环境。

## macOS 本地开发

`docker/launch_backend_service.sh` 为 Linux 设计（jemalloc `.so`、`LD_PRELOAD`），在 macOS 上无法直接跑。推荐直接运行：

```bash
cd ~/Opensource/forks/ragflow
docker compose -f docker/docker-compose-base.yml up -d          # 启动依赖
export PYTHONUNBUFFERED=1 PYTHONPATH=$(pwd) NLTK_DATA=./nltk_data
nohup .venv/bin/python api/ragflow_server.py > logs/backend.log 2>&1 &

cd web && nohup npm run dev > ../logs/frontend.log 2>&1 &
```

后端服务 `conf/service_conf.yaml` 里 host 全部是 `localhost`，与从宿主机直连依赖容器匹配，**不要**改成容器名（`mysql`、`es01` 等），那是 docker-compose.yml 全栈部署用的。

## 活跃文档

- `CLAUDE.md` — 项目说明书（本文件）
- `AGENTS.md` — 软链接 → `CLAUDE.md`
- `PLAN.md` — Agent 二改总体计划（Phase 0-3 路线图）
- `PLAN-phase2.md` — Phase 2 路线图总入口
- `PLAN-rbac.md` — P2.1 数据集 RBAC + 审计详细设计
- `PLAN-bot-channels.md` — P2.2 飞书机器人渠道详细设计
- `PLAN-multi-agent.md` — P2.3 Multi-Agent subagent 详细设计
- `PLAN-agent-runtime-maturity.md` — Phase 2.5 Agent Runtime 成熟化（Citation validator / 多轮上下文 / Agent definition manifest，参考 `~/Opensource/vendor/claude-code-ref/`）
- `PLAN-doc-ops.md` — Phase 2.6 文档运营工具（doc_tag / doc_rename / doc_archive / doc_reparse / doc_upload_from_url / kb_create + ask_user_question / submit_plan + sub_archivist）
- `PLAN-attachments.md` — Phase 2.7 附件协议 + 下载-验证-归档（对齐 claude-code-ref attachment-as-message pattern；`web_fetch_to_attachment` + `doc_archive_attachment` + `submit_plan.preview` 扩展）
- `PLAN-prompt-architecture.md` — Phase 2.8 prompt 系统重写（`PromptSection` + `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` + `tools/_names.py` 工具名常量 + 共享段库 + enabledTools 过滤 + 数字化长度锚点；参照 `vendor/claude-code-ref/src/constants/systemPromptSections.ts` + `prompts.ts` + `built-in/exploreAgent.ts`）
- `AUDIT-claude-code-alignment.md` — Phase 2.6 v0.3/v0.4：20 维度对齐 Claude Code 设计哲学，10 个用户没提到的发现（U1–U10），P0/P1/P2 分档；v0.4 §5-b 记录 runtime plan gate + tool annotations + next_steps 三项 T10-T14 落地
- `FINDINGS-phase-26-v02-live.md` — Phase 2.6 v0.2 活体测试的 3 个架构级 bug 修复记录
- `TEST-MANUAL-v0.4.md` — Phase 2.6 v0.4 人工测试清单（58 用例，G1-G12 分组，含 P0/P1/P2 优先级）
- `PRODUCT-UI-PLAN.md` — Phase 1.7 企业知识库前端产品化重构计划（已完成）
- `STATUS.md` — 会话交接，当前进度快照（保持 ≤ 5 个"最近更新"段；老条目挪到 `archive/HISTORY-phase-2.x.md`）
- `archive/HISTORY-phase-2.x.md` — STATUS 历史归档（仅追溯用，新内容不进这里）
- `DESIGN.md` — Phase 1 Agent v2 架构设计（稳定，不再改）
- `FORK.md` — 与上游 infiniflow/ragflow 的差异 + 已推送 commits
- `README-agent-v2.md` — Agent v2 最终用户/开发者使用指南
- `test/e2e/baojian_house.md` — 保障房 10 题黄金用例规范
- `api/agent_v2/tools/README.md` — 工具开发规范
- `web/CLAUDE.md` — 前端开发规范（shadcn/ui、i18n、React Query、样式调试）
- `README.md` / `docker/README.md` — 上游官方文档

## 首次运行记录

- **日期**: 2026-04-20
- **状态**: ✅ 成功（依赖服务 + 后端 API + 前端 dev server 全部就绪）
- **耗时**:
  - `uv sync` 首次 ~2–3 min（含失败重试）
  - `npm install` ~2 min（1962 包）
  - Docker 拉镜像 ~1–2 min（arm64 镜像全部可用）
  - 后端启动 38.9s（首次会从 HF Hub 下载 `InfiniFlow/deepdoc` 和 `text_concat_xgb_v1.0` 模型）
  - 前端 Vite 启动 1.5s
- **踩坑点**:
  - `graspologic @ git+https://gitee.com/infiniflow/graspologic.git@<sha>` 从国外访问 gitee 多次 RPC 中断。解决：在 `pyproject.toml` 加 `[tool.uv.sources]` 覆盖为 `github.com/infiniflow/graspologic` 同 SHA。
  - `launch_backend_service.sh` 用 `LD_PRELOAD` + `.so` 路径，macOS 不兼容。解决：绕过脚本，直接 `python api/ragflow_server.py`。
  - `download_deps.py` 仅为 Linux Docker 构建用（下载 Ubuntu `.deb`、Linux Chrome 等），macOS 本地开发只需 NLTK 数据：
    ```python
    import nltk
    for d in ['wordnet','punkt','punkt_tab']:
        nltk.download(d, download_dir='./nltk_data')
    ```
  - `MACOS=1` 已在 `docker/.env` 启用。
- **已知现象（正常）**:
  - `Warning: Failed to import module exesql: ... libodbc.2.dylib` — 未装 unixodbc，不影响 API。需要时 `brew install unixodbc`。
  - `WARNING:root:Load term.freq FAIL!` / `Realtime synonym is disabled, since no redis connection.` — 启动早期加载时 Redis 尚未连接，后续会正常连上。
  - `SECURITY WARNING: Using auto-generated SECRET_KEY.` — 开发环境无自定义 SECRET_KEY。

## Task Executor

`launch_backend_service.sh` 会在 server 之外并行跑 `rag/svr/task_executor.py <id>` worker 处理索引任务。macOS 本地如需测试文档解析 / 索引，另起终端：

```bash
cd ~/Opensource/forks/ragflow
export PYTHONPATH=$(pwd) NLTK_DATA=./nltk_data
.venv/bin/python rag/svr/task_executor.py 0
```

## Working Style (from upstream)

1. Think before acting. Read existing files before writing code.
2. Be concise in output but thorough in reasoning.
3. Prefer editing over rewriting whole files.
4. Do not re-read files you have already read.
5. Test your code before declaring done.
6. No sycophantic openers or closing fluff.
7. Keep solutions simple and direct.
8. User instructions always override this file.
