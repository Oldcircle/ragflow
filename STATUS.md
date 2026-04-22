# RAGFlow Agent 二改 — 进度快照

> **STATUS.md 是会话交接文档**，每次有实质进展时更新，只记录"当前状态 + 下一步入口"，不是历史日志。

---

## 最近更新：2026-04-22

**当前阶段**：**Phase 1 M1.4 已完成** — 前端 Agent 工作台页面（基于 Claude Design 知源设计稿）
**阻塞项**：无
**下一步入口**：M1.5（保障房 3 道黄金题端到端 + 调优）

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
