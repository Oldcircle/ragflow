# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RAGFlow is an open-source RAG (Retrieval-Augmented Generation) engine based on deep document understanding. It's a full-stack application with:
- Python backend (Flask-based API server)
- React/TypeScript frontend (built with UmiJS)
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
- **Runner**: `api/agent_v2/runner.py` — Claude Agent SDK 适配层；P2.5.2 起支持 `history=` / `summary_text=` 多轮参数
- **Event**: `api/agent_v2/event.py` — 统一 SSE 事件；P2.3 / P2.5.1 加了 `subagent_*` 和 `citation_warning`
- **Tools**: `api/agent_v2/tools/` — 5 个 MCP 工具（rag_retrieve / rag_list_docs / rag_read_doc / rag_graph_query / spawn_subagent）
- **Validators** (P2.5.1): `api/agent_v2/validators/` — EvidenceIndex + CitationValidator（三规则 missing_chunk / number_unsupported / no_citation_for_numeric）+ `rewrite.py` strict-mode 一次性重写
- **Compactor** (P2.5.2): `api/agent_v2/compactor.py` — 历史摘要压缩；`run_compact_safely` 入口把任何异常写进 access_audit_log（action=`agent_v2.compact`），fire-and-forget 不再静默失败
- **Definitions** (P2.5.3): `api/agent_v2/definitions/` — 声明式 AgentDefinition schema + 6 supervisor + 2 subagent built-in
- **Bot Channels** (P2.2): `api/bot_channels/` — 飞书 webhook adapter（签名 + 会话映射）
- **HTTP App**: `api/apps/agent_v2_app.py` — 注册 `/v1/agent_v2/*` blueprint

### Frontend (`/web/`)
- React/TypeScript with UmiJS framework
- Ant Design + shadcn/ui components
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
- `PRODUCT-UI-PLAN.md` — Phase 1.7 企业知识库前端产品化重构计划（已完成）
- `STATUS.md` — 会话交接，当前进度快照
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
