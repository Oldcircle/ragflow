# RAGFlow Agent v2 — 使用指南

> Agent-first 企业知识库助手，基于 Claude Agent SDK + RAGFlow 的 RAG 引擎。
> 原 RAGFlow Dialog 功能并存不替换，本页只覆盖 Agent v2。

## 一、它是什么

**一句话**：把 RAG 作为 Agent 可调用的工具，而不是"每轮强制拼进 prompt"。
- LLM 自主决定何时检索（事实相关问题必查、闲聊不查）
- 多轮迭代（查不到换关键词再查）
- 输出严格基于检索结果，不编数字
- 支持 DeepSeek / Claude 等模型

和 RAGFlow 原版 Dialog 的区别：

| 维度 | 原 Dialog | Agent v2 |
|---|---|---|
| 检索策略 | 每轮强制一次 | 模型自主，可多轮换关键词 |
| 防幻觉 | 依赖 prompt + 引用插入 | System Prompt 严格约束 + 工具级"未查到"信号 |
| 工具链 | 单 RAG 工具 | 4 个工具：retrieve / graph_query / list_docs / read_doc |
| 可观测性 | 日志为主 | 前端右侧实时展示每次工具调用的 args + result |
| 扩展 | 不易 | Python `@tool` 注册即可 |

## 二、快速开始

### 前置条件

1. RAGFlow 已跑起来（见项目 `CLAUDE.md`）
2. 在 RAGFlow **模型供应商** 页面添加至少一个 Chat 模型：
   - DeepSeek（推荐，便宜）— 会自动走 `/anthropic` 兼容端点
   - Anthropic — 直接用 Claude 官方
   - OpenAI-API-Compatible / VLLM / Ollama（需对方支持 Anthropic 协议）

   > Agent v2 不再依赖 `AGENT_V2_DEEPSEEK_KEY` 等环境变量。
   > 旧 env var 作为兜底保留，但你可以在「模型供应商」页改 key，Agent 立刻生效。
3. 知识库已至少有一个解析完成的 KB（Embedding 建议 `bge-m3`）

### 打开工作台

浏览器访问 **<http://localhost:9222/agent-chat>** 或顶部导航栏点「工作台」。

### 新建一个 Agent 会话

1. 左侧 **新建会话**
2. **可选：顶部选一个模板**（保障房 / 政策 / 法务 / 研报 / 客服 / Wiki）
   - 模板会自动填好 System Prompt 和默认参数
3. 填写剩下的：
   - 名称（可改模板自带的）
   - 知识库：勾选 ≥ 1 个（多选需共享同一 embedding 模型）
   - 模型：从下拉选你配过的 Chat 模型
4. **创建**

### 对话

- 中间区域发消息，支持 Markdown 渲染（加粗、表格、代码块、数学公式）
- Agent 答复里带有 **`[1] [2]` 上标** 的地方表示引用对应的 chunk；点击上标自动滚动到下方引用卡片
- 答复下方会出现 **「引用来源」** 卡片，列出用到的政策/文档 + 可展开的 chunk 列表（含相似度 + 页码）
- 右侧 **工具调用** 面板实时展示 Agent 调用的每个工具（args / result / 耗时 / 状态）
- 点右上角 **停止** 可中断流式响应

## 三、推荐 System Prompt 模板

默认模板已包含严格防幻觉指令，新建会话时不用改。若想自定义：

```
你是 <领域> 顾问，严格基于知识库内容答复。

工作方式：
1. 判断问题是否涉及本领域；无关则礼貌拒答。
2. 相关问题：必须调用 rag_retrieve 工具检索原文，可多次换关键词。
3. 严格基于检索结果答复：
   - 所有数字、年限、比例、金额必须原文支撑
   - 原文没写的内容，回答"未查到相关规定，建议咨询 <业务方>"
   - 不要把其他场景的规则套到本知识库

禁止：
- 编造数字、名称、时间
- 用训练知识补充原文未说的内容
```

## 四、工具清单（当前 4 个）

| 工具 | 用途 | Agent 调用时机 |
|---|---|---|
| `rag_retrieve` | 语义检索相关片段（向量+BM25 融合） | 问题涉及 KB 内容时必调 |
| `rag_list_docs` | 列出 KB 里的所有文档元数据 | 问"有哪些文件""一共几份"时 |
| `rag_read_doc` | 按 doc_id 读整份文档（分页） | 需要完整上下文时 |
| `rag_graph_query` | GraphRAG 实体/关系查询 | 需要跨文档链式推理时（KB 须启用 KG） |

新增工具：在 `api/agent_v2/tools/` 放一个 `@tool` 装饰的 async 函数 → 注册到 `registry.py` → 前端会自动在会话里可用。

## 五、后端端点

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/v1/agent_v2/session` | 创建会话 |
| GET | `/v1/agent_v2/session` | 列我的会话 |
| GET | `/v1/agent_v2/session/<id>` | 会话详情 + 消息 + 工具调用 |
| DELETE | `/v1/agent_v2/session/<id>` | 软删 |
| GET | `/v1/agent_v2/tool` | 列所有工具 |
| GET | `/v1/agent_v2/model` | 列用户 TenantLLM 中的 Chat 模型 |
| GET | `/v1/agent_v2/template` | 列 6 个预置 Agent 模板 |
| POST | `/v1/agent_v2/conversation` | 发消息（SSE 流式） |

所有端点要登录 cookie。SSE 事件类型见 `api/agent_v2/event.py`。

## 六、程序化使用（SDK / CLI）

### Python CLI

```bash
export AGENT_V2_DEEPSEEK_KEY=sk-xxx
export NLTK_DATA=./nltk_data

.venv/bin/python scripts/test_agent_v2.py \
  --provider deepseek \
  --question "你的问题" \
  --max-turns 8
```

### 在代码里嵌

```python
from api.agent_v2.runner import AgentRunner, ModelConfig

runner = AgentRunner(
    tenant_id="<tenant_uuid>",
    kb_ids=["<kb_uuid>"],
    system_prompt="...",
    model=ModelConfig(
        model="deepseek-chat",
        base_url="https://api.deepseek.com/anthropic",
        auth_token="sk-xxx",
    ),
    max_turns=8,
    max_budget_usd=0.5,
)
async for ev in runner.run("用户问题"):
    print(ev.to_dict())
```

事件类型：`text_delta` / `thinking` / `tool_call_start` / `tool_call_end` / `error` / `end`

## 七、性能 & 成本（实测）

从保障房 10 题黄金评测（见 `test/e2e/baojian_house_results_20260422.md`）：

- 单次会话耗时：12–125s（推理题拖后腿）
- 平均每题 35.6s
- 平均每题 3.2 次工具调用
- DeepSeek 成本：平均 $0.11 / 题
- **质量：10 题平均 4.8/5**（防幻觉 3 题全满分）

## 八、常见问题

**Q：工具调用面板没更新？**
A：M1.4 之前的 bug，M1.4 修了不可变更新。硬刷新（Cmd+Shift+R）。

**Q：Agent 不调用 `rag_retrieve`？**
A：检查 System Prompt 是否明确"必须检索"。

**Q：Markdown 不渲染？**
A：已在 M1.4 接入 react-markdown。硬刷新。

**Q：DeepSeek 端点连不上？**
A：验证 `AGENT_V2_DEEPSEEK_KEY` 有效；DeepSeek 的 Anthropic 兼容端点在 `https://api.deepseek.com/anthropic`。

**Q：多个 KB 报错"embedding 不一致"？**
A：`rag_retrieve` 限制所有 KB 必须用同一个 embedding 模型。把不同 embedding 的 KB 拆到不同会话。

## 九、开发者文档

- `PLAN.md` — 整体路线图（Phase 0-3）
- `DESIGN.md` — Phase 1 架构设计
- `STATUS.md` — 当前进度
- `FORK.md` — 与上游 infiniflow/ragflow 的差异
- `api/agent_v2/tools/README.md` — 工具开发规范
- `test/agent_v2/` — pytest 单元测试
- `test/e2e/baojian_house.md` — 保障房黄金用例
- `scripts/run_baojian_golden.py` — 批量跑分脚本

## 十、协议

Apache 2.0，随 RAGFlow 上游。
