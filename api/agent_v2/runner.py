"""AgentRunner — Claude Agent SDK 的适配层。

把 RAGFlow 的 tenant/kb 上下文喂给 SDK，
把 SDK 的异步消息流翻译成统一 Event 序列。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from . import event as ev
from .errors import AgentError
from .registry import MCP_SERVER_NAME, build_mcp_server, list_tool_names
from .tools.base import ToolContext, reset_ctx, set_ctx

logger = logging.getLogger("ragflow.agent_v2.runner")


@dataclass
class ModelConfig:
    """Agent 用的模型配置。

    - Anthropic 原生：``base_url=None, auth_token=<anthropic_key>``
    - DeepSeek：``base_url="https://api.deepseek.com/anthropic"``
                ``auth_token=<deepseek_key>``
                ``model="deepseek-chat"``
    """

    model: str = "claude-sonnet-4-5"
    fallback_model: str | None = None
    base_url: str | None = None
    auth_token: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)


class AgentRunner:
    """最小版 Agent 运行器（M1.1 阶段）。

    用法::

        runner = AgentRunner(
            tenant_id="...",
            kb_ids=["..."],
            system_prompt="你是...",
            model=ModelConfig(model="claude-sonnet-4-5", auth_token="..."),
        )
        async for event in runner.run("用户问题"):
            print(event.to_dict())
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        kb_ids: list[str],
        system_prompt: str,
        model: ModelConfig | None = None,
        tool_names: list[str] | None = None,
        user_id: str | None = None,
        max_turns: int = 20,
        max_budget_usd: float | None = 1.0,
        permission_mode: str = "bypassPermissions",
    ):
        if not tenant_id:
            raise AgentError("tenant_id is required")
        if not kb_ids:
            raise AgentError("at least one kb_id is required")

        self.tenant_id = tenant_id
        self.kb_ids = kb_ids
        self.system_prompt = system_prompt
        self.model = model or ModelConfig()
        self.tool_names = tool_names  # None = 全部启用
        self.user_id = user_id
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.permission_mode = permission_mode

    def _build_options(self) -> ClaudeAgentOptions:
        mcp_server = build_mcp_server(enabled=self.tool_names)
        allowed = (
            [f"mcp__{MCP_SERVER_NAME}__{n}" for n in self.tool_names]
            if self.tool_names is not None
            else list_tool_names()
        )

        # 只有模型 provider 相关的 env 才走到子进程；
        # RAGFlow 业务上下文用 ContextVar 注入（因为 MCP 工具在本进程跑）。
        env = {}
        if self.model.base_url:
            env["ANTHROPIC_BASE_URL"] = self.model.base_url
        if self.model.auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = self.model.auth_token
        env.update(self.model.extra_env)

        return ClaudeAgentOptions(
            model=self.model.model,
            fallback_model=self.model.fallback_model,
            system_prompt=self.system_prompt,
            mcp_servers={MCP_SERVER_NAME: mcp_server},
            allowed_tools=allowed,
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            permission_mode=self.permission_mode,  # type: ignore[arg-type]
            env=env,
        )

    async def run(self, user_message: str) -> AsyncIterator[ev.Event]:
        """发一条用户消息，异步返回事件流。"""
        options = self._build_options()
        logger.info(
            "AgentRunner.run: tenant=%s kb_ids=%s model=%s turn_limit=%d",
            self.tenant_id,
            self.kb_ids,
            self.model.model,
            self.max_turns,
        )

        tool_call_starts: dict[str, float] = {}

        ctx = ToolContext(
            tenant_id=self.tenant_id,
            kb_ids=tuple(self.kb_ids),
            user_id=self.user_id,
        )
        token = set_ctx(ctx)
        try:
            async for msg in query(prompt=user_message, options=options):
                async for event in self._translate(msg, tool_call_starts):
                    yield event
        except Exception as exc:  # noqa: BLE001 — Runner 要吞所有异常转成事件
            logger.exception("AgentRunner.run failed")
            yield ev.error(code=type(exc).__name__, message=str(exc))
            yield ev.end()
        finally:
            reset_ctx(token)

    async def _translate(
        self, msg, tool_call_starts: dict[str, float]
    ) -> AsyncIterator[ev.Event]:
        """把 SDK 消息翻译成统一 Event。"""
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    yield ev.text_delta(block.text)
                elif isinstance(block, ThinkingBlock):
                    yield ev.thinking(block.thinking)
                elif isinstance(block, ToolUseBlock):
                    tool_call_starts[block.id] = time.time()
                    yield ev.tool_call_start(
                        tool_id=block.id, name=block.name, args=block.input
                    )
        elif isinstance(msg, UserMessage):
            # UserMessage 里可能包含 tool_result（SDK 把工具结果以 user role 返回）
            content = msg.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        start = tool_call_starts.pop(block.tool_use_id, None)
                        duration_ms = (
                            int((time.time() - start) * 1000) if start else None
                        )
                        yield ev.tool_call_end(
                            tool_id=block.tool_use_id,
                            result=self._extract_tool_result_text(block),
                            error=("error" if block.is_error else None),
                            duration_ms=duration_ms,
                        )
        elif isinstance(msg, ResultMessage):
            usage = {
                "input_tokens": getattr(msg, "input_tokens", None)
                or getattr(msg.usage, "input_tokens", None)
                if getattr(msg, "usage", None)
                else None,
                "output_tokens": getattr(msg, "output_tokens", None)
                or getattr(msg.usage, "output_tokens", None)
                if getattr(msg, "usage", None)
                else None,
                "total_cost_usd": getattr(msg, "total_cost_usd", None),
                "duration_ms": getattr(msg, "duration_ms", None),
                "num_turns": getattr(msg, "num_turns", None),
            }
            yield ev.end(usage=usage)
        elif isinstance(msg, SystemMessage):
            # 初始化/订阅等信息，M1.1 暂不往前端传
            pass
        elif isinstance(msg, StreamEvent):
            # 低级流式事件（include_partial_messages=True 时才有），暂略
            pass
        else:
            logger.debug("AgentRunner: unhandled message type %s", type(msg).__name__)

    @staticmethod
    def _extract_tool_result_text(block: ToolResultBlock) -> str | dict | None:
        """从 ToolResultBlock 中提取文本/结构化内容。"""
        content = getattr(block, "content", None)
        if content is None:
            return None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
                else:
                    parts.append(str(p))
            return "\n".join(parts)
        return str(content)
