# FORK.md — 与上游 infiniflow/ragflow 的差异和同步策略

> 本文件追踪：我们的 fork 相对上游做了哪些改动、哪些文件冲突风险高、如何定期 merge upstream。

---

## 基本信息

| 项 | 值 |
|---|---|
| 上游仓库 | <https://github.com/infiniflow/ragflow> |
| 我方 fork | <https://github.com/Oldcircle/ragflow> |
| 本地路径 | `~/Opensource/forks/ragflow` |
| 起始基线 | `upstream/main` @ v0.24.0（2026-04-21 fork） |
| 协议 | Apache 2.0 |

## Git Remote 配置

```
origin   → https://github.com/Oldcircle/ragflow.git  (fetch + push)
upstream → https://github.com/infiniflow/ragflow.git (fetch + push)
```

## 同步策略

### 上游追踪节奏

| 场景 | 频率 |
|---|---|
| 主动关注 upstream release | 每 2 周看一次 |
| 定期 merge | 每月一次（大版本则临时 merge） |
| 紧急 CVE / 安全补丁 | 随时 |

### Merge 流程

```bash
cd ~/Opensource/forks/ragflow

# 1. 拉上游
git fetch upstream

# 2. 切一个临时分支做 merge（避免污染 main 和 feat/*）
git checkout -b sync/upstream-$(date +%Y%m%d) main

# 3. 合并
git merge upstream/main

# 4. 解冲突（见下方「冲突热点」）
# 5. 跑测试（至少黄金 e2e）
# 6. PR 到 origin/main
```

### 冲突热点（提前心理准备）

| 文件 / 目录 | 冲突概率 | 原因 |
|---|---|---|
| `pyproject.toml` | 高 | 依赖频繁升级；我们加了 `[tool.uv.sources]` 覆盖 |
| `api/apps/__init__.py` | 高 | 我们注册了 `agent_v2_app` blueprint |
| `web/src/routes.tsx` | 中 | 我们加了 `/agent-chat` 路由 |
| `docker/.env` | 中 | 我们启用了 `MACOS=1` |
| `CLAUDE.md` | 中 | 上游改上游的，我们补 Fork/端口/首次运行记录 |
| `api/db/db_models.py` | 低-中 | 我们只在文件尾部加表，冲突小 |
| `agent/component/*` | 低 | 我们不改原画布组件 |

### 冲突解决原则

1. **上游删了的我们要保留的**：先看是否必要；若必要，把它搬到我们的 `api/agent_v2/` 下
2. **上游改的我们改的同一处**：优先接受上游，再把我们的改动重新 apply
3. **我们新增的文件**：不会冲突
4. **新增依赖**：手动合并，注意版本范围兼容

---

## 差异清单

### A. 构建 / 环境（已 commit 或待 commit）

#### `pyproject.toml`

**改动**：新增 `[tool.uv.sources]` 段，强制 `graspologic` 从 github 拉而非 gitee。

```toml
[tool.uv.sources]
graspologic = { git = "https://github.com/infiniflow/graspologic.git", rev = "38e680cab72bc9fb68a7992c3bcc2d53b24e42fd" }
```

**原因**：gitee.com 对境外 IP 不稳定，多次 `git fetch` RPC 中断导致 `uv sync` 失败。github 同 SHA 镜像稳定。

**同步策略**：每次 upstream 升级 `graspologic` 版本时，同步更新 rev，保留从 github 拉。

---

#### `docker/.env`

**改动**：启用 `MACOS=1`。

```diff
- # MACOS=1
+ MACOS=1
```

**原因**：本地 macOS 开发专属优化（上游注释里建议 macOS 用户启用）。

**同步策略**：upstream 改其他 env 时不影响此行，低冲突风险。

---

### B. 文档（项目说明书级）

#### `CLAUDE.md`

**改动**：保留上游所有段，**在文末追加**以下段：
- `## Fork 信息`（本 Fork 和 upstream 的关系）
- `## 端口`（各服务端口说明）
- `## macOS 本地开发`（绕过 `launch_backend_service.sh` 的启动方式）
- `## 活跃文档`（指向 PLAN/STATUS/DESIGN/FORK）
- `## 首次运行记录`（日期、耗时、踩坑点、已知现象）
- `## Task Executor`（macOS 单独启 worker 的方法）

**冲突**：上游几乎不改 `CLAUDE.md`（他们的 CLAUDE.md 面向通用开发者）。若冲突，保留双方内容。

---

#### `AGENTS.md`

**改动**：上游原本是独立文件（GitHub Copilot 用），我们改成 `AGENTS.md → CLAUDE.md` 软链接。

**原因**：工作区规范要求所有 Agent 读同一份指令文件；原 AGENTS.md 内容已并入 CLAUDE.md。

**同步策略**：upstream 若更新 AGENTS.md，仅参考新内容更新 CLAUDE.md，不恢复独立文件。

---

### C. 新增文件（零冲突）

| 文件 | 用途 |
|---|---|
| `PLAN.md` | 二改总体计划 |
| `STATUS.md` | 会话交接 |
| `DESIGN.md` | Phase 1 架构设计 |
| `FORK.md`（本文件） | Fork 差异记录 |
| `logs/` | 本地运行日志（.gitignore 里） |
| `nltk_data/` | NLTK 数据（.gitignore 里） |

---

### D. Phase 1 实际落地改动（feat/agent-v2 分支）

**commits 已推送**：
- `3e68fcb` chore: fork setup for macOS local development
- `f403cdf` docs: add agent v2 project planning and fork docs
- `e72760a` feat(agent-v2): M1.1 minimal runner with rag_retrieve tool
- `1832b5f` feat(agent-v2): M1.2 complete tool set + unit tests + docs
- `529b75a` feat(agent-v2): M1.3 HTTP API + DB persistence + SSE streaming
- `ddb4b3a` feat(agent-v2): M1.4 frontend workbench with 知源 design language
- `194be96` fix(agent-v2): tool_call live update + markdown + references
- `4781e50` feat(agent-v2): M1.5 golden test suite — 10 questions, avg 4.8/5
- `470e31e` refactor(agent-v2): replace env-var model keys with TenantLLM lookup (M1.6 Step 1 BE)
- `3418bad` refactor(agent-v2): wire NewSessionDialog to /v1/agent_v2/model endpoint (M1.6 Step 1 FE)
- `4c45929` feat(agent-v2): inline [N] citation footnotes with hover+click highlight (M1.6 Step 3)
- `824ee28` feat(agent-v2): M1.6 Step 2 — Agent template system (6 presets)
- `ec8afc200` feat(ui): rebrand shell as enterprise knowledge base
- `a91f546bc` feat(ui): switch product shell to sidebar workspace

#### 后端（已落地）

| 文件 / 目录 | 性质 | 冲突风险 |
|---|---|---|
| `api/agent_v2/` 整个目录（runner/event/errors/registry + tools/*） | 新增 7 文件 | 无 |
| `api/apps/agent_v2_app.py` | 新增（自动注册到 `/v1/agent_v2`） | 无 |
| `api/db/db_models.py` | 文末加 3 张表（AgentV2Session/Message/ToolCall） | 低 |
| `api/db/services/agent_v2_service.py` | 新增 CRUD | 无 |
| `pyproject.toml` | + `claude-agent-sdk>=0.1.64` + graspologic github 源 | 中 |

#### 前端（已落地，采用 Claude Design「知源」设计语言）

| 文件 / 目录 | 性质 | 冲突风险 |
|---|---|---|
| `web/src/pages/agent-chat/` 整个目录（theme/api/hooks/components） | 新增 15 文件 | 无 |
| `web/src/routes.tsx` | + `Routes.AgentChat` + route entry | 中 |
| `web/src/layouts/components/global-navbar.tsx` | + 菜单项「工作台」 | 中 |
| `web/src/locales/en.ts` + `zh.ts` | + `agentV2.*` 40+ keys | 中 |

#### 测试（已落地）

| 文件 | 覆盖 |
|---|---|
| `test/agent_v2/test_base.py` | ContextVar + 截断（9 tests） |
| `test/agent_v2/test_event.py` | 事件构造（9 tests） |
| `test/agent_v2/test_registry.py` | 工具注册（5 tests） |
| `test/agent_v2/test_tool_schemas.py` | schema 健全性（14 tests） |
| `test/agent_v2/test_service.py` | CRUD（3 真 MySQL tests，需 `RAGFLOW_TEST_DB=1`） |
| `scripts/test_agent_v2.py` | Agent 端到端 CLI |
| `scripts/test_tools_direct.py` | 4 工具直接调用 smoke |
| `scripts/test_agent_v2_e2e.py` | 三表持久化 E2E |

### E. 本地开发资料（不入库）

- `design-refs/zhiyuan/` — Claude Design 稿原始包（Linear/Vercel 风格「知源·企业知识库」）
  - 来源：<https://api.anthropic.com/v1/design/h/gQYxR8tIy5QvB6Idf8nhWA>
  - 改造思路：采纳设计语言（tokens / 3 列布局 / 思考块 / chip 发送器），替换右侧栏语义（引用 → 工具调用），不照搬 KB/图谱/检索测试页
  - 已加入 `.gitignore`，不会被推到 GitHub

### F. Phase 1.7 前端产品化重构（进行中）

目标：把 RAGFlow 原版前端外壳重构成我们自己的企业知识库产品前端。保留全部功能、路由、API 和后端能力，只替换 UI / UX / 信息架构 / 文案。

参考源：`design-refs/zhiyuan/project/知源 · 企业知识库.html`。

首批改造范围：
- `web/src/layouts/*` — 全局品牌 shell 和导航
- `web/src/pages/login-next/*` — 登录页品牌化
- `web/src/pages/home/*` — 企业知识库首页概览
- `web/src/locales/zh.ts` / `web/src/locales/en.ts` — 产品文案
- `web/src/global.less` — 全局视觉 tokens 和滚动条

已推送：
- `ec8afc200`：产品品牌、文档计划、登录页、首页、顶部导航首批改造；随后恢复暗色主题兼容
- `a91f546bc`：全局 shell 从顶部导航切换为左侧企业工作台 sidebar

进行中（未推送）：
- `web/src/pages/next-chats/chat/*`：原「对话」详情页向 `/agent-chat` 工作台风格对齐
- `web/src/pages/next-chats/chat/styles.css`：对话工作台样式层，复用暗色主题 token

---

### G. Phase 2 落地（feat/agent-v2 分支，已推送）

**2026-04-23 一整天把 Phase 2 / 3.1 / 3.2 / 2.5 全部落地**：

#### Phase 2 commits

- `<RBAC commit>` feat(rbac): P2.1 — dataset access + audit log + 3 key path patches
- `<bot commit>` feat(bot): P2.2 — Feishu webhook adapter + conversation map
- `bd780f682` feat(agent-v2): P2.3 — Multi-Agent spawn_subagent tool + live trace UI

#### Phase 3.1 合规 + 运维基线

- `345e66c66` feat(audit): P3.1a — audit log admin UI
- `a9b1a999e` feat(quota): P3.1b — tenant quota + daily usage metering + admin UI
- `1e1524493` feat(rate-limit): P3.1c — APIToken rate limiting + api_requests metering

#### Phase 3.2 定时触发器

- `fa11f4721` feat(trigger): P3.2 — scheduled agent triggers (cron → Feishu / audit)

#### Phase 2.5 Agent Runtime 成熟化（详见 `PLAN-agent-runtime-maturity.md`）

- `540bfb91f` feat(agent-v2): P2.5.1 — citation validator + evidence index + UI warning
- `86fb8e867` feat(agent-v2): P2.5.2 — multi-turn context + compact summary
- `c921ea729` feat(agent-v2): P2.5.3 — Agent Definition manifest + named subagents
- `c2e420a62` docs: Phase 2.5 complete — update STATUS + runtime-maturity plan

#### 新增后端文件（全部零冲突）

| 目录/文件 | 性质 | 来自 |
|---|---|---|
| `api/agent_v2/validators/{evidence_index,citation}.py` | P2.5.1 Citation Validator | `540bfb91f` |
| `api/agent_v2/compactor.py` | P2.5.2 历史摘要压缩 | `86fb8e867` |
| `api/agent_v2/definitions/{schema,registry}.py` | P2.5.3 Agent Definition schema + 注册表 | `c921ea729` |
| `api/agent_v2/definitions/built_in/*.py` | P2.5.3 — 6 supervisor + 2 subagent 内置定义 | `c921ea729` |
| `api/agent_v2/tools/spawn_subagent.py` | P2.3 新增；P2.5.3 扩展 `subagent_type` 路由 | `bd780f682` / `c921ea729` |
| `api/bot_channels/` 整个包（feishu adapter + signature + parser + client） | P2.2 | |
| `api/apps/bot_app.py` / `api/apps/bot_channel_app.py` | P2.2 | |
| `api/db/services/{dataset_access,audit_log,bot_channel,bot_conversation_map,subagent_trace,tenant_quota,agent_trigger}_service.py` | Phase 2/3.1/3.2 服务层 | |

#### 新增 DB 表（全部自动迁移）

| 表 | Phase | 
|---|---|
| `dataset_access` / `access_audit_log` | P2.1 |
| `bot_channel` / `bot_conversation_map` | P2.2 |
| `agent_v2_subagent_trace` | P2.3 |
| `tenant_quota` / `tenant_usage_daily` | P3.1b |
| `agent_trigger` | P3.2 |

#### 新增 `agent_v2_session` 列（全部 nullable，ALTER TABLE 就位）

| 列 | Phase |
|---|---|
| `citation_enforce_level` / `citation_numeric_strict` | P2.5.1 |
| `history_turn_limit` / `summary_text` / `summary_until_seq` | P2.5.2 |

#### 新增 SSE 事件类型（前端 `use-agent-stream.ts` 已对接）

- `subagent_start` / `subagent_end` — P2.3
- `citation_warning` — P2.5.1

#### 新增前端页面 / 组件

| 路径 | Phase |
|---|---|
| `/dataset/dataset-member/:id` | P2.1 |
| `/user-setting/audit-log` | P3.1a |
| `/user-setting/usage` | P3.1b |
| `/user-setting/bot-channels` | P2.2 |
| `/user-setting/triggers` | P3.2 |
| `CitationWarningPanel` 组件 + 子 Agent `SubagentInline` 内嵌卡 | P2.5.1 / P2.3 |

---

## 长期差异策略

### 永远不合入上游的内容
- 我们的 `PLAN.md` / `STATUS.md` / `FORK.md`（属于我们的项目管理）
- `CLAUDE.md` 中的 Fork/首次运行 专属段
- `kb-data/` 引用（在 gitignore 里）
- `logs/`（在 gitignore 里）

### 考虑贡献回上游的
- Agent v2 的某些通用工具（若打磨到生产级）
- macOS 启动脚本改造（若上游还没做）
- pyproject.toml graspologic github 源（若上游也觉得 gitee 不稳）

### 永远从上游接受的
- 安全补丁
- RAG 核心改进（DeepDoc / GraphRAG）
- 新增 LLM provider 接入
- 向量引擎更新

---

## 冲突预案手册

### 场景 1：pyproject.toml 合并冲突

```bash
# 先看冲突
git diff --name-only --diff-filter=U

# 手动编辑 pyproject.toml：
# 1. 接受上游对 dependencies 块的更改
# 2. 保留我们的 [tool.uv.sources] 块
# 3. 更新 graspologic rev 为 upstream 新版本的 SHA（去 github 搜）

# 验证
uv sync --python 3.12 --all-extras --dry-run
```

### 场景 2：api/apps/__init__.py 冲突（blueprint 注册）

```python
# 典型冲突：上游加了新 blueprint，我们也加了 agent_v2
# 解决：两个都保留
from .kb_app import manager as kb_manager
from .upstream_new_app import manager as new_manager  # 接受上游
from .agent_v2_app import manager as agent_v2_manager  # 保留我们的
```

### 场景 3：上游改了我们依赖的服务（如 RetrievalService）

**风险**：我们的 Tool 基于 RetrievalService 的 API 写，若上游改接口，Tool 会挂。

**应对**：
- 在 `api/agent_v2/tools/rag_retrieve.py` 顶部写清依赖的上游 API 版本和函数签名
- 每次 merge 后跑 e2e 黄金用例
- 若签名破坏，在工具层做 adapter

---

## 提交规范（本 fork）

- 用 Conventional Commits（feat/fix/docs/chore/refactor）
- 分支命名：`feat/xxx`、`fix/xxx`、`sync/upstream-YYYYMMDD`
- PR 到 `origin/main`，不直接 push
- 提交前跑：`ruff check` + `pytest` 最小集

---

## 版本记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-04-21 | v0.1 | 初始 Fork；记录环境/pyproject/macOS 适配改动 |
| 2026-04-22 | v0.2 | Phase 1 全部完成（M1.1-M1.6）；Phase 1.7 前端产品化重构 |
| 2026-04-23 | v0.3 | Phase 2 (RBAC/bot/multi-agent) + Phase 3.1 (审计/配额/限流) + Phase 3.2 (Cron 触发器) + Phase 2.5 (citation validator / 多轮 / Agent Definition) 全数落地 |
