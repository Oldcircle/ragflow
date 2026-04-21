# Agent v2 工具集

此目录下的每个 `.py` 文件定义一个可以被 Claude Agent SDK 调用的 **MCP 工具**。
Agent 通过对工具 schema 的理解，自主决定何时以及以什么参数调用哪个工具。

## 架构速览

```
用户请求
   │
   ▼
AgentRunner.run(message)            # api/agent_v2/runner.py
   │  set_ctx(ToolContext)          # api/agent_v2/tools/base.py
   ▼
claude_agent_sdk.query(...)         # 子进程 Claude Code CLI
   │  LLM 推理 → "我要调 rag_retrieve"
   │  MCP tool call via stdio
   ▼
SdkMcpTool.handler(args)            # 本进程内 MCP In-Process Server
   │  get_ctx() 读出 tenant/kb      # ← ContextVar（同事件循环传播）
   │  调 RAGFlow 服务：Dealer.retrieval() / DocumentService / ...
   ▼
返回 MCP tool-result（JSON 文本）
```

## 为什么用 ContextVar 而不是 env vars

`ClaudeAgentOptions.env` 注入的环境变量只进入 **Claude Code CLI 子进程**，
但我们的 MCP 工具运行在 **SDK 调用方的 Python 进程**（In-Process Server）。
这是两个进程空间。用 `contextvars.ContextVar` 可以在同事件循环的异步任务里
自动传播，工具可以拿到 Runner 注入的 `ToolContext`。

## 工具清单

### 1. `rag_retrieve` — 语义检索（最核心）

```
用途: 在知识库中搜索与 query 最相关的原文片段（向量 + BM25 融合）
输入: {query: str, top_n?: int=8, similarity_threshold?: float=0.15}
输出: {query, total, chunks[{doc_name, doc_id, content, similarity, page, position}], doc_aggs}
底层: rag/nlp/search.py  Dealer.retrieval()
```

Agent 调用策略：涉及知识库内容时必调；多轮换关键词也是合理策略。

### 2. `rag_list_docs` — 文档清单

```
用途: 列出当前会话所有可见 KB 下的文档（名称 + 元数据）
输入: {keywords?: str, page?: int=1, page_size?: int=50}
输出: {total_matched, returned, page, page_size, documents[{doc_id, name, type, chunk_num, ...}]}
底层: api/db/services/document_service.py  DocumentService.get_list()
```

Agent 调用策略：当用户问"有哪些文件"，或需要先掌握文档全貌再决定查哪份时调。

### 3. `rag_read_doc` — 读整文档

```
用途: 按 doc_id 读文档的完整或部分内容（按 chunk_order_int 排序）
输入: {doc_id: str, chunk_offset?: int=0, chunk_limit?: int=50}
输出: {doc_id, doc_name, kb_id, total_chunks, returned_chunks, chunks[{order, page, content}]}
底层: rag/nlp/search.py  Dealer.chunk_list()
权限: 自动校验 doc_id 必须属于当前会话授权的 kb_ids
```

Agent 调用策略：语义检索命中了某份关键文档、需要看完整条款时调。
注意输出受 32KB 截断限制，对长文档需分段读取（调整 `chunk_offset`）。

### 4. `rag_graph_query` — GraphRAG 实体查询

```
用途: 在知识图谱中按问题/实体检索相关实体 + 关系 + 社区摘要
输入: {query: str, ent_topn?: int=6, rel_topn?: int=6, max_token?: int=4096}
输出: {query, graph_context, raw_keys}
底层: rag/graphrag/search.py  KGSearch.retrieval()
前提: KB 建索引时必须勾选 Knowledge Graph
```

Agent 调用策略：跨文档链式推理场景用；普通问题先用 rag_retrieve，
不够再升级到 graph。KB 未启用图谱时会返回空 graph_context（不报错）。

## 通用约定

### 输入

所有工具通过 `input_schema` 声明 JSON Schema；Agent 调用时传 `args: dict`。
工具函数签名统一 `async def fn(args: dict) -> dict`（由 `@tool` 装饰）。

### 输出

统一 MCP tool-result 格式：

```python
{"content": [{"type": "text", "text": "<JSON or plain text>"}]}
```

用 `mcp_json_response(obj)` / `mcp_text_response(text)` 生成。

### 上下文

工具通过 `get_ctx()` 获取 `ToolContext`：

```python
from .base import get_ctx

async def my_tool(args: dict) -> dict:
    ctx = get_ctx()                # 默认要求 tenant_id + kb_ids
    # ctx.tenant_id / ctx.kb_ids / ctx.user_id
```

要求字段可以显式传入 `get_ctx(require=["tenant_id"])`。

### 大小限制

`mcp_json_response()` 默认截断到 32KB（`MAX_TOOL_OUTPUT_BYTES`）以避免炸
Agent 上下文。对可能返回大量文本的工具（如 `rag_read_doc`），设计上应当
让 Agent 分页取。

### 错误处理

工具出错两种模式：

1. **业务错误**（如 KB 不存在、参数无效）：返回 `{"error": "..."}` JSON，
   **不抛异常**。这让 Agent 知道错误原因并继续推理。
2. **系统级异常**（DB 断连、模型调用失败等）：直接 raise，Runner 会捕获
   并转成 `tool_call_end(error=...)` 事件。

## 新增工具步骤

1. 在本目录新建 `my_tool.py`
2. 用 `@tool(name, description, input_schema)` 装饰 async 函数
3. 在 `api/agent_v2/registry.py` 的 `ALL_TOOLS` 字典注册
4. 在 `scripts/test_tools_direct.py` 加一个 smoke 调用
5. 在本 README 加一条工具描述

## 测试

- 直接 smoke：`python scripts/test_tools_direct.py`（绕过 Agent，验证纯工具逻辑）
- Agent 端到端：`python scripts/test_agent_v2.py`（让 Agent 自主选工具）
- 单元测试：`pytest tests/agent_v2/`（正在补齐）
