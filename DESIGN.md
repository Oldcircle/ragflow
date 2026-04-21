# Agent v2 架构设计（Phase 1）

> 本文档是 Phase 1 MVP 的详细技术设计。Phase 2/3 的架构演进另起文档。
> 对应 `PLAN.md` 的 Phase 1 章节。

---

## 〇、SDK 与内部 API 核查（2026-04-21 补）

### claude-agent-sdk 真实特性（0.1.64）

| 项 | 实际行为 |
|---|---|
| 身份 | 官方 Python SDK for **Claude Code**（不是通用 Agent 框架） |
| 运行方式 | 每次 `query()` spawn 一个 bundled Claude Code CLI 子进程，通过 stdio + JSON-RPC 通信 |
| 工具协议 | **MCP**（Model Context Protocol），用 `@tool` 装饰 + `create_sdk_mcp_server()` 注册 |
| 模型 | 默认 Claude；第三方通过 `ANTHROPIC_BASE_URL` 环境变量重定向到 Anthropic 兼容端点 |
| DeepSeek | 提供 `https://api.deepseek.com/anthropic/v1` Anthropic 兼容端点，可用但可能丢失 thinking/cache 特性 |
| 关键入口 | `query(prompt, options)` 异步迭代器 / `ClaudeSDKClient` 有状态客户端 |
| Options | `ClaudeAgentOptions(model, mcp_servers, system_prompt, max_turns, max_budget_usd, ...)` |
| 权限模型 | `PermissionMode`: `default/acceptEdits/plan/bypassPermissions/dontAsk/auto` + `can_use_tool` 回调 |
| Hooks | `PreToolUse / PostToolUse / Stop / Notification / PermissionRequest` 等 10+ 钩子 |
| 会话 | 内置 `SessionStore` 接口 + `continue_conversation / resume / fork_session` |

**简化**：原设计里的 `ToolRegistry` 不必自造——直接用 `@tool` 装饰函数 + `create_sdk_mcp_server` 聚合即可。

### RAGFlow 内部服务（真实调用点）

| 服务 | 入口 | 签名 |
|---|---|---|
| 检索 | `rag/nlp/search.py:37` `class Dealer` | `async retrieval(question, embd_mdl, tenant_ids, kb_ids, page, page_size, similarity_threshold=0.2, vector_similarity_weight=0.3, top=1024, doc_ids=None, aggs=True, rerank_mdl=None, highlight=False, rank_feature=...)` |
| GraphRAG | `rag/graphrag/search.py:35` `class KGSearch(Dealer)` | `async retrieval(question, tenant_ids, kb_ids, emb_mdl, llm, max_token=8196, ent_topn=6, rel_topn=6, comm_topn=1, ent_sim_threshold=0.3, rel_sim_threshold=0.3, **kwargs)` |
| 文档列表 | `api/db/services/document_service.py:45` `class DocumentService` | `get_list(kb_id, page_number, items_per_page, orderby, desc, keywords, id, name, ...)` |
| 文档文件 | `api/apps/sdk/doc.py:117-118` | `File2DocumentService.get_storage_address(doc_id) → (id, location)` + `settings.STORAGE_IMPL.get(id, location)` |
| LLMBundle | `api/db/services/llm_service.py` | `LLMBundle(tenant_id, LLMType.XXX)` — 按 tenant 和类型拿模型 |
| 登录装饰 | `api/apps/__init__.py:158-193` | `@login_required`（JWT 或 APIToken） |
| 响应格式 | `api/apps/__init__.py:247` | `get_json_result(code, message, data)` 返回 `{"code":..., "message":..., "data":...}` |
| Blueprint | `api/apps/__init__.py:253-302` 自动扫描 `*_app.py` | 约定：文件内有 `manager = Blueprint(...)` |
| SSE 模式 | `api/apps/restful_apis/chat_api.py:1066-1080` | `Response(stream(), mimetype="text/event-stream")` + `async for ans in ...: yield "data:" + json.dumps(...) + "\n\n"` |
| DB 模型 | `api/db/db_models.py` `class DataBaseModel` | peewee ORM，无自动 migration；新表继承 DataBaseModel |

---

## 一、目标与范围

### 功能目标
- 在 RAGFlow 后端内置 **Claude Agent SDK** 运行时
- 把 RAGFlow 的 RAG/GraphRAG 能力包装成 Agent 可调用的 Tool
- 前端新增「Agent 工作台」页面（不动原有 Dialog / 画布）
- 支持流式输出（思考链 + 工具调用可视化）

### 非目标（Phase 1 不做）
- Multi-Agent 协作
- Trigger（定时/事件）
- 企业功能（租户/权限/审计）
- 工具开放给第三方扩展（Phase 2）
- 自定义 Agent 图形化编排（用原版 Agent 画布即可，Agent v2 是代码定义）

### 设计原则

1. **并存不替换**：原 Dialog 和 Agent 画布完全不动，Agent v2 是新增路径
2. **Python 单栈**：不引入 Node/Rust 辅助进程
3. **数据隔离**：新表、新 API 路径、新前端路由；不复用原表
4. **工具即函数**：每个 Tool 是一个 Python 函数 + JSON Schema
5. **可观测**：每步工具调用落库 + 集成 Langfuse

---

## 二、整体架构

### 模块布局

```
ragflow/
├── api/
│   ├── apps/
│   │   └── agent_v2_app.py          🆕 HTTP Blueprint（/v1/agent_v2/*）
│   ├── agent_v2/                    🆕 Agent Runtime 模块
│   │   ├── __init__.py
│   │   ├── runner.py                ClaudeAgentRunner（SDK 适配）
│   │   ├── registry.py              ToolRegistry
│   │   ├── session.py               Session 持久化
│   │   ├── event.py                 事件协议（文本/工具调用/结束）
│   │   ├── errors.py
│   │   └── tools/
│   │       ├── __init__.py
│   │       ├── base.py              @tool 装饰器 + 通用基类
│   │       ├── rag_retrieve.py      🔑 核心：KB 检索
│   │       ├── rag_graph_query.py   🔑 GraphRAG 实体查询
│   │       ├── rag_list_docs.py     🔑 列文档
│   │       └── rag_read_doc.py      🔑 读整文档
│   └── db/
│       └── db_models.py             在最下方扩展 Agent v2 三张表
│
├── web/src/
│   ├── pages/
│   │   └── agent-chat/              🆕 Agent 工作台页面
│   │       ├── index.tsx
│   │       ├── components/
│   │       │   ├── message-list.tsx
│   │       │   ├── tool-call-card.tsx
│   │       │   ├── input-box.tsx
│   │       │   └── session-sidebar.tsx
│   │       ├── hooks/
│   │       │   └── use-agent-stream.ts
│   │       └── api.ts
│   └── routes.tsx                   改：注册 /agent-chat 路由
│
├── PLAN.md
├── STATUS.md
├── DESIGN.md（本文件）
└── FORK.md
```

### 运行时数据流

```
用户                          前端                    后端                  外部
 │                             │                       │                     │
 │  发送消息                    │                       │                     │
 │────────────────────────────▶│                       │                     │
 │                             │  POST /v1/agent_v2/   │                     │
 │                             │  conversation (SSE)   │                     │
 │                             │──────────────────────▶│                     │
 │                             │                       │ ClaudeAgentRunner   │
 │                             │                       │ ┌──────────────────┐│
 │                             │                       │ │ SDK.run(...)     ││
 │                             │                       │ │ ┌─ LLM 推理 ──┐ ││
 │                             │                       │ │ │ (决定调工具) │ ││
 │                             │                       │ │ └─────────────┘ ││
 │                             │   event: text chunk    │ │                  ││
 │                             │◀──────────────────────│ │                  ││
 │    文本流显示                │                       │ │                  ││
 │◀────────────────────────────│                       │ │                  ││
 │                             │                       │ │ ┌─ tool_call ──┐ ││
 │                             │                       │ │ │ retrieve()   │ ││
 │                             │                       │ │ └─┬────────────┘ ││
 │                             │                       │ │   │              ││
 │                             │                       │ │   ▼              ││
 │                             │                       │ │ RAGFlow         ││
 │                             │                       │ │ Retrieval       ││
 │                             │                       │ │ Service         ││
 │                             │                       │ │   │              ││
 │                             │                       │ │   ▼              ││
 │                             │                       │ │ ES / MySQL      ││
 │                             │                       │ │   │              ││
 │                             │                       │ │ ┌─┘              ││
 │                             │                       │ │ │ tool_result   ││
 │                             │                       │ │ ▼                ││
 │                             │   event: tool_call    │ │                  ││
 │                             │◀──────────────────────│ │                  ││
 │   工具调用可视化             │                       │ │                  ││
 │◀────────────────────────────│                       │ │                  ││
 │                             │                       │ │ ┌─ LLM 推理 ──┐ ││
 │                             │                       │ │ │（综合答案） │  ││
 │                             │                       │ │ └─────────────┘ ││
 │                             │   event: text chunk    │ │                  ││
 │                             │◀──────────────────────│ │                  ││
 │   最终答案显示               │                       │ │                  ││
 │◀────────────────────────────│                       │ └──────────────────┘│
 │                             │   event: end          │                     │
 │                             │◀──────────────────────│ save to MySQL      │
 │                             │                       │ trace to Langfuse  │
```

---

## 三、核心模块设计

### 3.1 `api/agent_v2/runner.py`

**职责**：Claude Agent SDK 的适配层。把 RAGFlow 的 tenant/kb 上下文喂给 SDK；从 SDK 的异步消息流翻译成统一 Event。

**关键代码骨架**（基于 SDK 0.1.64 真实 API）：

```python
# api/agent_v2/runner.py（示意，M1.1 会落成真代码）
from claude_agent_sdk import (
    query, ClaudeAgentOptions, create_sdk_mcp_server,
    AssistantMessage, UserMessage, SystemMessage, ResultMessage,
    TextBlock, ToolUseBlock, ToolResultBlock,
)
from .tools.rag_retrieve import rag_retrieve
from .event import Event  # 我们统一的事件类型

class AgentRunner:
    def __init__(self, *, tenant_id, kb_ids, system_prompt,
                 model="claude-sonnet-4-5", max_turns=20,
                 max_budget_usd=1.0, extra_env=None):
        self.tenant_id = tenant_id
        self.kb_ids = kb_ids
        self.system_prompt = system_prompt
        self.model = model
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.extra_env = extra_env or {}

    async def run(self, user_message: str):
        # 组装工具集（MCP 内进程 server）
        mcp_server = create_sdk_mcp_server(
            name="ragflow-tools", version="0.1.0",
            tools=[rag_retrieve]  # @tool 装饰过的函数
        )
        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=self.system_prompt,
            mcp_servers={"ragflow": mcp_server},
            allowed_tools=["mcp__ragflow__rag_retrieve"],
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            permission_mode="bypassPermissions",  # 服务端不弹权限框
            env={
                "RAGFLOW_TENANT_ID": self.tenant_id,
                "RAGFLOW_KB_IDS": ",".join(self.kb_ids),
                **self.extra_env,
            },
        )
        async for msg in query(prompt=user_message, options=options):
            # msg 可能是 UserMessage/AssistantMessage/SystemMessage/ResultMessage/StreamEvent
            yield self._translate(msg)
```

**切换模型**：通过 `ClaudeAgentOptions.env` 注入 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` 即可切 DeepSeek 或其他 Anthropic 兼容端点。

**Event 类型**（`event.py`）：

| type | payload |
|---|---|
| `text_delta` | `{text: str}` — LLM 流式文本增量 |
| `tool_call_start` | `{id, name, args}` |
| `tool_call_end` | `{id, result, error?, duration_ms}` |
| `thinking` | `{text: str}` — 思考过程（用 Claude extended thinking 时） |
| `error` | `{code, message}` |
| `end` | `{usage: {input_tokens, output_tokens, cached_tokens}}` |

**模型支持**：
- 先支持 Anthropic (Claude)
- DeepSeek：用 SDK 的"OpenAI 兼容端点"模式 + prompt 适配
- OpenAI / Gemini 同理

**限制机制**：
- `max_iterations`：Agent 循环上限（防死循环）
- `max_tokens_budget`：整会话 token 预算
- 工具级超时（单工具默认 30s）

### 3.2 `api/agent_v2/registry.py`

**职责**：聚合所有 RAGFlow 工具到一个 MCP server 实例。

SDK 已提供 `create_sdk_mcp_server(name, version, tools=[...])`，不需要我们自造 Registry。本文件只保留一个工厂函数：

```python
# api/agent_v2/registry.py
from claude_agent_sdk import create_sdk_mcp_server
from .tools.rag_retrieve import rag_retrieve
# 后续 M1.2 加入其他工具
# from .tools.rag_graph_query import rag_graph_query
# from .tools.rag_list_docs import rag_list_docs
# from .tools.rag_read_doc import rag_read_doc

def build_ragflow_mcp_server(enabled: list[str] | None = None):
    all_tools = {"rag_retrieve": rag_retrieve}
    tools = list(all_tools.values()) if enabled is None \
            else [all_tools[n] for n in enabled if n in all_tools]
    return create_sdk_mcp_server("ragflow-tools", "0.1.0", tools=tools)
```

### 3.3 `api/agent_v2/tools/base.py`

**直接使用 SDK 自带的 `@tool` 装饰器**：

```python
# api/agent_v2/tools/base.py（小工具集）
import os
from claude_agent_sdk import tool

def get_ctx():
    """从环境变量读取 Runner 注入的 RAGFlow 上下文"""
    return {
        "tenant_id": os.environ.get("RAGFLOW_TENANT_ID"),
        "kb_ids": [x for x in os.environ.get("RAGFLOW_KB_IDS", "").split(",") if x],
    }

__all__ = ["tool", "get_ctx"]
```

**实际工具定义样式**（见 `rag_retrieve.py`）：

```python
# api/agent_v2/tools/rag_retrieve.py
from .base import tool, get_ctx

@tool(
    name="rag_retrieve",
    description="在企业知识库中检索与 query 语义相关的原文片段。用于查政策、条款、说明等。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词"},
            "top_n": {"type": "integer", "default": 8},
            "similarity_threshold": {"type": "number", "default": 0.2},
        },
        "required": ["query"],
    },
)
async def rag_retrieve(args: dict) -> dict:
    ctx = get_ctx()
    # ... 调 RAGFlow Dealer.retrieval()
    return {"content": [{"type": "text", "text": ...}]}  # MCP tool 输出格式
```

**工具契约**：
- 必须异步
- 返回 JSON-serializable 字典
- 失败时 raise `ToolError`（被 Runner 捕获并转 tool_call_end 的 error 字段）
- 最大输出 size 限制（避免炸 Context）：默认 32KB

### 3.4 四个核心 Tool 细节

#### `rag_retrieve.py`
```
输入：query (str), kb_id (str), top_n (int = 8), similarity_threshold (float = 0.2)
调用：复用 RAGFlow 现有 RetrievalService.search()
输出：
  {
    "chunks": [
      {"doc_name": str, "content": str, "similarity": float, "doc_id": str},
      ...
    ],
    "total": int,
  }
```

#### `rag_graph_query.py`
```
输入：entity (str), kb_id (str), depth (int = 2)
调用：复用 RAGFlow GraphRAG 模块
输出：
  {
    "entity": str,
    "related": [{"entity": str, "relation": str, "weight": float}, ...],
    "context_chunks": [...],
  }
```

#### `rag_list_docs.py`
```
输入：kb_id (str), filter (str, optional)
调用：RAGFlow DocumentService.list_by_kb()
输出：
  {
    "documents": [{"id": str, "name": str, "type": str, "chunk_num": int}, ...],
    "total": int,
  }
```

#### `rag_read_doc.py`
```
输入：doc_id (str), page_start (int = 0), page_end (int, optional)
调用：RAGFlow MinIO + chunk 重组
输出：
  {
    "doc_id": str,
    "doc_name": str,
    "content": str,       # 拼接后的原文
    "page_range": [int, int],
    "total_pages": int,
  }
```

### 3.5 `api/agent_v2/session.py`

**职责**：会话持久化 + 上下文管理。

**能力**：
- 创建/加载/删除会话
- 消息历史管理（写入 `agent_v2_message` 表）
- 超长历史压缩（调 LLM 生成摘要 + 替换）
- 工具调用记录落库

### 3.6 `api/apps/agent_v2_app.py`

**HTTP 路由**：

```
POST   /v1/agent_v2/session              创建会话
GET    /v1/agent_v2/session              列会话
GET    /v1/agent_v2/session/<id>         会话详情 + 消息历史
DELETE /v1/agent_v2/session/<id>         删除会话

POST   /v1/agent_v2/conversation         发消息（SSE 流式返回事件）
POST   /v1/agent_v2/conversation/abort   中断正在跑的会话

GET    /v1/agent_v2/tool                 列工具
GET    /v1/agent_v2/config/default       默认配置（model、system_prompt）
POST   /v1/agent_v2/config/validate      验证自定义配置
```

**返回格式**（对齐 RAGFlow 原有约定）：
```json
{ "code": 0, "data": {...}, "message": "" }
```

**SSE 事件格式**：
```
event: text_delta
data: {"text": "..."}

event: tool_call_start
data: {"id": "...", "name": "rag_retrieve", "args": {...}}

event: tool_call_end
data: {"id": "...", "result": {...}, "duration_ms": 123}

event: end
data: {"usage": {...}}
```

---

## 四、数据库 Schema

所有新表前缀 `agent_v2_`，不改动任何原表。

### 4.1 `agent_v2_session`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(32) PK | UUID |
| `tenant_id` | VARCHAR(32) | 继承 RAGFlow 概念 |
| `user_id` | VARCHAR(32) | 创建者 |
| `name` | VARCHAR(255) | 会话名（可自动生成） |
| `kb_ids` | JSON | 允许访问的 KB 列表（用于 Tool 权限） |
| `tool_names` | JSON | 启用的工具名白名单 |
| `system_prompt` | TEXT | |
| `model_config` | JSON | `{provider, model, api_key_id}` |
| `max_iterations` | INT | 默认 20 |
| `max_tokens_budget` | INT | 默认 100000 |
| `status` | VARCHAR(16) | `active` / `archived` |
| `create_time` | BIGINT | |
| `update_time` | BIGINT | |

### 4.2 `agent_v2_message`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(32) PK | |
| `session_id` | VARCHAR(32) FK | |
| `role` | VARCHAR(16) | `user` / `assistant` / `system` |
| `content` | MEDIUMTEXT | 文本正文 |
| `thinking` | MEDIUMTEXT | 扩展思考文本（可空） |
| `tool_calls` | JSON | 该条消息触发的工具调用 ID 列表 |
| `usage` | JSON | `{input_tokens, output_tokens, cached_tokens}` |
| `create_time` | BIGINT | |
| INDEX | `(session_id, create_time)` | |

### 4.3 `agent_v2_tool_call`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(32) PK | |
| `session_id` | VARCHAR(32) FK | |
| `message_id` | VARCHAR(32) FK | 归属的 assistant 消息 |
| `tool_name` | VARCHAR(64) | |
| `args` | JSON | |
| `result` | MEDIUMTEXT | JSON 字符串 |
| `error` | TEXT | |
| `status` | VARCHAR(16) | `success` / `error` / `timeout` |
| `duration_ms` | INT | |
| `start_time` | BIGINT | |
| INDEX | `(session_id, start_time)` | |

---

## 五、前端设计

### 5.1 路由

新增 `/agent-chat`（不动老的 `/chat` 和 `/agent`）。

### 5.2 页面结构

```
┌────────────────────────────────────────────────────────────────────┐
│  顶栏：返回 / 切换会话 / 设置                                       │
├────────────┬───────────────────────────────────────────────────────┤
│            │                                                       │
│  会话      │    消息流                                              │
│  侧栏      │                                                       │
│            │    ┌────────────────────────────────────┐            │
│  + 新会话  │    │  用户：保障房申请条件                │            │
│            │    └────────────────────────────────────┘            │
│  📋 保障房  │                                                       │
│  📋 客服    │    ┌──────────────────────────────────┐             │
│  📋 研报    │    │  助手思考中...                    │             │
│  📋 ...     │    │  🔧 调用 rag_retrieve              │             │
│            │    │     query="公租房申请条件"          │             │
│            │    │     kb_id="..."                    │             │
│            │    │     ▼ 展开结果（3 个片段）          │             │
│            │    │  🔧 调用 rag_retrieve              │             │
│            │    │     query="社保年限"               │             │
│            │    │     ▼ 展开结果（1 个片段）          │             │
│            │    │                                     │             │
│            │    │  综合回答：...                      │             │
│            │    │                                     │             │
│            │    │  本回答依据：                       │             │
│            │    │  [1] 深圳市公租房管理办法           │             │
│            │    │  [2] 深圳市保障性住房条例           │             │
│            │    └──────────────────────────────────┘             │
│            │                                                       │
│            │    ┌────────────────────────────────────┐            │
│            │    │  输入框  [发送]                     │            │
│            │    └────────────────────────────────────┘            │
└────────────┴───────────────────────────────────────────────────────┘
```

### 5.3 关键组件

- **`message-list.tsx`**：消息列表，区分 user/assistant
- **`tool-call-card.tsx`**：工具调用卡片（可展开看 args 和 result）
- **`input-box.tsx`**：输入框（支持多行、粘贴、快捷键）
- **`session-sidebar.tsx`**：会话侧栏（列表、搜索、新建、删除）

### 5.4 SSE 处理

用 `use-agent-stream.ts` hook 封装：

```typescript
// 仅示意
const { messages, isStreaming, send, abort } = useAgentStream({
  sessionId,
  onToolCall: (call) => {...},
  onError: (err) => {...},
});
```

---

## 六、与原 RAGFlow 的关系

| 维度 | 策略 |
|---|---|
| 路由 | 新路径 `/v1/agent_v2/*`，不改原 `/v1/conversation/*` |
| 前端 | 新页面 `/agent-chat`，不改原 `/chat` 和 `/agent` |
| 数据库 | 新表，不改原表结构 |
| 模型接入 | 复用原有「模型供应商」配置（不重复造轮子） |
| KB / 文档 / Retrieval | 复用（作为 Tool 的底层实现） |
| 鉴权 | 复用 RAGFlow 的用户体系和 API Key |

**升级路径**（给现有用户）：
- 现有 Dialog 用户不受任何影响
- 在左侧菜单新增「Agent 工作台」入口
- 可一键从现有 Dialog 迁移：读其关联 KB 和 Prompt，生成 Agent v2 会话

---

## 七、依赖与成本

### 新增 Python 依赖
```
claude-agent-sdk>=0.1.0   # Anthropic 官方 Python SDK
sse-starlette             # （如果现有 Quart 不够用）
```

### 运行时成本（预估）
- **Claude Sonnet 4**：$3 / 1M input, $15 / 1M output
- **DeepSeek**：¥1 / 1M input, ¥8 / 1M output
- 单次"保障房深度查询"预计：
  - 输入 8-15k tokens（含工具 schema）
  - 输出 1-3k tokens
  - 工具调用 2-5 次
  - Claude 成本：$0.05-0.15 / 次
  - DeepSeek 成本：¥0.05-0.2 / 次

### Token 预算策略
- 默认单会话预算：100k tokens
- 超预算：自动压缩历史（保留 system + 最近 10 轮 + 摘要）
- 单次工具结果截断：32KB

---

## 八、测试策略

| 层 | 内容 |
|---|---|
| 单元测试 | 每个 Tool 独立测试（mock RAGFlow service） |
| Runner 测试 | Mock SDK，测 Event 流转、超时、预算 |
| 集成测试 | 真跑一轮 DeepSeek / Claude 调用，用保障房场景做 fixture |
| 前端测试 | SSE 流处理、工具调用可视化、断流重连 |
| 端到端 | 保障房 3 道题黄金用例 |

**黄金用例**（对应 STATUS.md 的 3 道题）：
- 事实题：区别题
- 推理题：你的资格题
- 防幻觉题：首付比例题

每次代码改动必须跑黄金用例，结果贴到 `tests/e2e/baojian_house.md`。

---

## 九、Langfuse 集成

RAGFlow 已有 Langfuse 依赖，我们扩展用法：

- 每次 Agent run = 一个 Langfuse Trace
- 每轮 LLM 调用 = 一个 Generation
- 每次 Tool 调用 = 一个 Span
- Trace 元数据：`session_id`, `user_id`, `tenant_id`, `kb_ids`

调试时直接打开 Langfuse 面板可见完整调用链。

---

## 十、里程碑与验收

| 里程碑 | 验收标准 |
|---|---|
| M1.1 | `runner.py` 跑通：发一条 user message，Agent 调用 `rag_retrieve` 一次，返回文本 |
| M1.2 | 4 个工具全部实现 + 各自单测通过 |
| M1.3 | HTTP endpoint 可用：前端能发请求、接收 SSE 流 |
| M1.4 | 前端页面完整：侧栏 + 消息流 + 工具可视化 + 输入框 |
| M1.5 | 端到端：保障房 3 道黄金题全部合格（人工打分 ≥ 4/5） |
| M1.6 | 对外 demo：非技术同事/朋友试用 15 分钟无障碍 |

---

## 十一、已知未解决问题

- [ ] DeepSeek 通过 OpenAI 兼容端点接 Claude Agent SDK 时，Function Calling 参数格式是否一致需验证
- [ ] 工具结果过大时的截断策略（截掉 vs 摘要 vs 分页继续查）需 Phase 1 中期决定
- [ ] Agent 中断后的恢复（用户刷新页面）— Phase 1 先做"失败则从头来"，Phase 2 做 Checkpoint

---

## 十二、版本记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-04-21 | v0.1 | 初稿 |
