# RAGFlow Agent 二改 — 进度快照

> **STATUS.md 是会话交接文档**，每次有实质进展时更新，只记录"当前状态 + 下一步入口"，不是历史日志。

---

## 最近更新：2026-04-22（深夜 +1）

**当前阶段**：**Phase 1.7 前端产品化重构进行中**（A/B 完成；C 全部小批完成）
**下一步入口**：P1.7-D — 搜索 `/searches`、Agent 编排 `/agents`、记忆 `/memories`、文件 `/files`、用户设置 `/profile-setting/*`

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
