"""AgentRunner — Claude Agent SDK 的适配层。

把 RAGFlow 的 tenant/kb 上下文喂给 SDK，
把 SDK 的异步消息流翻译成统一 Event 序列。
"""

from __future__ import annotations

import asyncio
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
        session_id: str | None = None,
        parent_session_id: str | None = None,
        depth: int = 0,
        citation_enforce_level: str = "warn",
        citation_numeric_strict: bool = True,
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

        # Phase 2.3: track session + depth for subagent 上下文传递
        self.session_id = session_id
        self.parent_session_id = parent_session_id
        self.depth = depth

        # Phase 2.5.1 — citation validator
        self.citation_enforce_level = citation_enforce_level or "warn"
        self.citation_numeric_strict = bool(citation_numeric_strict)

        # 子发事件会 emit 到这个 queue，父在 run() loop 里把它们穿插进自己的 SDK 流
        self._event_bus: asyncio.Queue[ev.Event] | None = None

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

    async def run(
        self,
        user_message: str,
        *,
        history: list[dict] | None = None,
        summary_text: str | None = None,
    ) -> AsyncIterator[ev.Event]:
        """发一条用户消息，异步返回事件流。

        Phase 2.5.2 — multi-turn context
        ================================

        - ``history``：同一 session 的最近若干条消息，升序；每条形如
          ``{"role": "user"|"assistant", "content": "...", "thinking": "..."}``。
          传 ``None`` 或空 list 表示单轮 QA（与 2.5.2 之前行为一致）。
        - ``summary_text``：可选，compact 后的历史摘要字符串。如果有，会塞到
          <conversation-history> 前面。

        注意：Claude Agent SDK ``query()`` 单次只接受一个 ``prompt`` 字符串，
        无法像 Chat API 那样直接传 ``messages=[...]``；所以我们走**合成 user
        message** 路径——把历史拼成 <conversation-history> 前置块，后面再跟
        <current-user-message>。这是 claude-code-ref 的 ``forkSubagent.ts``
        里也在用的模式。
        """
        prompt = self._build_prompt_with_history(
            user_message, history=history, summary_text=summary_text
        )
        options = self._build_options()
        logger.info(
            "AgentRunner.run: tenant=%s kb_ids=%s model=%s turn_limit=%d depth=%d",
            self.tenant_id,
            self.kb_ids,
            self.model.model,
            self.max_turns,
            self.depth,
        )

        tool_call_starts: dict[str, float] = {}

        # 工具（如 spawn_subagent）往 bus 里推事件，父这里穿插转发。
        self._event_bus = asyncio.Queue()

        async def emit(event: ev.Event) -> None:
            await self._event_bus.put(event)  # type: ignore[union-attr]

        # 跟踪当前正在执行的 tool call id（给 ctx.current_tool_call_id）
        # — SDK 的 tool_use id 在 AssistantMessage 里出现、工具执行时需要读
        tool_current_holder: dict[str, str] = {}

        ctx = ToolContext(
            tenant_id=self.tenant_id,
            kb_ids=tuple(self.kb_ids),
            user_id=self.user_id,
            session_id=self.session_id,
            system_prompt=self.system_prompt,
            tool_names=(
                tuple(self.tool_names) if self.tool_names is not None else None
            ),
            model_config=self.model,
            max_budget_usd=self.max_budget_usd,
            depth=self.depth,
            subagent_count_this_turn=0,
            event_emitter=emit,
            current_tool_call_id=None,
        )
        token = set_ctx(ctx)

        async def sdk_iter() -> AsyncIterator[ev.Event]:
            async for msg in query(prompt=prompt, options=options):
                async for event in self._translate(
                    msg, tool_call_starts, tool_current_holder, ctx,
                ):
                    yield event

        # Phase 2.5.1 — build EvidenceIndex + final text during the stream
        from .validators import EvidenceIndex, validate_citations

        evidence = EvidenceIndex()
        text_parts: list[str] = []
        tool_name_by_id: dict[str, str] = {}

        try:
            async for event in _merge_streams(sdk_iter(), self._event_bus):
                # ── intercept text_delta + tool_call_* for the validator ──
                if event.type == "text_delta":
                    text_parts.append(event.data.get("text") or "")
                elif event.type == "tool_call_start":
                    tid = event.data.get("id")
                    tname = event.data.get("name") or ""
                    if tid:
                        tool_name_by_id[tid] = tname
                elif event.type == "tool_call_end":
                    tid = event.data.get("id")
                    tname = tool_name_by_id.get(tid, "")
                    _collect_evidence_from_tool(
                        evidence, tname, event.data.get("result")
                    )

                if event.type == "end":
                    # 在 end 之前跑校验 + （strict 模式下）追一次 rewrite
                    if self.citation_enforce_level != "off":
                        final_text = "".join(text_parts)
                        issues = validate_citations(
                            final_text,
                            evidence,
                            numeric_strict=self.citation_numeric_strict,
                        )
                        if issues:
                            async for post_ev in self._handle_citation_issues(
                                final_text=final_text,
                                issues=issues,
                                evidence=evidence,
                            ):
                                yield post_ev
                yield event
        except Exception as exc:  # noqa: BLE001 — Runner 要吞所有异常转成事件
            logger.exception("AgentRunner.run failed")
            yield ev.error(code=type(exc).__name__, message=str(exc))
            yield ev.end()
        finally:
            reset_ctx(token)

    async def _translate(
        self,
        msg,
        tool_call_starts: dict[str, float],
        tool_current_holder: dict[str, str] | None = None,
        ctx: ToolContext | None = None,
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
                    # 让 spawn_subagent 等工具知道自己是哪个 tool_use
                    if ctx is not None:
                        ctx.current_tool_call_id = block.id
                    if tool_current_holder is not None:
                        tool_current_holder["id"] = block.id
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

    def _build_prompt_with_history(
        self,
        user_message: str,
        *,
        history: list[dict] | None,
        summary_text: str | None,
    ) -> str:
        """把历史 + 摘要拼成一个 SDK 能吃的 prompt 字符串。

        结构：

            [若有 summary_text]
            <conversation-summary>
            {summary_text}
            </conversation-summary>

            [若有 history]
            <conversation-history>
            [1] user: ...
            [1] assistant: ...
            ...
            </conversation-history>

            <current-user-message>
            {user_message}
            </current-user-message>

        没有 history 也没有 summary 时，就退化为原始 ``user_message`` 字符串，
        保证行为与 2.5.2 之前完全一致。
        """
        has_summary = bool(summary_text and summary_text.strip())
        has_history = bool(history)

        if not has_summary and not has_history:
            return user_message

        parts: list[str] = []
        if has_summary:
            parts.append(
                "<conversation-summary>\n"
                f"{summary_text.strip()}\n"
                "</conversation-summary>"
            )
        if has_history:
            lines: list[str] = []
            idx = 0
            for m in history or []:
                role = m.get("role") or ""
                if role not in ("user", "assistant"):
                    continue
                idx += 1
                content = (m.get("content") or "").strip()
                if not content:
                    continue
                # 对 assistant 消息保留但截断——太长的历史回答对追问不相关的细节
                # 反而是噪音，压到 1200 字内够识别 topic + 引用编号
                if role == "assistant" and len(content) > 1200:
                    content = content[:1200] + " …（已截断）"
                lines.append(f"[#{idx}] {role}: {content}")
            if lines:
                parts.append(
                    "<conversation-history>\n"
                    + "\n".join(lines)
                    + "\n</conversation-history>"
                )
        parts.append(
            "<current-user-message>\n"
            f"{user_message}\n"
            "</current-user-message>\n\n"
            "请基于上面的对话历史理解用户的指代关系（如「刚才」「上面那条」）"
            "再回答 <current-user-message>。当需要事实依据时，仍然调用工具检索。"
        )
        return "\n\n".join(parts)

    async def _handle_citation_issues(
        self,
        *,
        final_text: str,
        issues: list,
        evidence,
    ) -> AsyncIterator[ev.Event]:
        """P2.5.1 citation 校验失败后的事件序列生成器。

        - warn 模式：直接 yield 一条 ``citation_warning`` level=warn，不追改
        - strict 模式：尝试一次 rewrite（HTTP POST /v1/messages），再 validator 复检
          - 复检通过 → yield text_delta（追加校正段）+ ``citation_warning`` level=strict_rewritten
          - 复检仍失败 / rewrite 调用失败 → yield text_delta（降级文案）+ ``citation_warning`` level=strict_failed
        """
        from .validators import validate_citations
        from .validators.rewrite import rewrite_answer_strict

        if self.citation_enforce_level != "strict":
            yield ev.citation_warning(
                issues=[i.to_dict() for i in issues],
                level="warn",
            )
            return

        # strict 模式：尝试一次 rewrite
        rewritten = await rewrite_answer_strict(
            original_text=final_text,
            issues=issues,
            evidence=evidence,
            model=self.model.model,
            base_url=self.model.base_url,
            auth_token=self.model.auth_token or "",
        )

        if rewritten:
            # 复检：重写后的文本也必须过 validator
            recheck = validate_citations(
                rewritten,
                evidence,
                numeric_strict=self.citation_numeric_strict,
            )
            if not recheck:
                # 追加校正段给前端渲染
                banner = (
                    "\n\n---\n**🛡️ 校正答复（strict 模式自动重写，已通过 citation 校验）**\n\n"
                )
                yield ev.text_delta(banner + rewritten + "\n")
                yield ev.citation_warning(
                    issues=[i.to_dict() for i in issues],
                    level="strict_rewritten",
                )
                return
            # 复检仍失败 → 视为 strict_failed，也把重写后发现的新问题合并上报
            issues = list(issues) + list(recheck)

        # 降级：追加一段对用户说清"数字没能核实"的提示
        fallback = (
            "\n\n---\n"
            "**⚠️ 系统提示**：本次答复中出现的部分数字 / 年限 / 金额在检索结果中"
            "找不到直接出处（strict 模式自动校验）。该部分请视为"
            "**未经核实**，建议以官方政策原文或向相应部门咨询为准。\n"
        )
        yield ev.text_delta(fallback)
        yield ev.citation_warning(
            issues=[i.to_dict() for i in issues],
            level="strict_failed",
        )

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


_SENTINEL_SDK_DONE = object()


async def _merge_streams(
    sdk_events: AsyncIterator[ev.Event],
    bus: asyncio.Queue,
) -> AsyncIterator[ev.Event]:
    """交错 SDK 事件流 + 工具自发的事件队列（来自 spawn_subagent 等）。

    Pattern：用一个后台 task 把 SDK 事件塞进 bus，主循环只 get bus。
    SDK 结束时塞一个 sentinel；drain 完 sentinel 后退出。
    """
    async def _sdk_to_bus() -> None:
        try:
            async for event in sdk_events:
                await bus.put(event)
        except Exception as e:
            await bus.put(_SdkError(e))
        finally:
            await bus.put(_SENTINEL_SDK_DONE)

    sdk_task = asyncio.create_task(_sdk_to_bus())
    # SDK 结束时直接 drain bus 完成；工具尾声事件数量不需要显式追踪
    try:
        while True:
            event = await bus.get()
            if event is _SENTINEL_SDK_DONE:
                # SDK 端结束；剩下都是工具尾声事件
                while not bus.empty():
                    nxt = bus.get_nowait()
                    if isinstance(nxt, _SdkError):
                        raise nxt.exc
                    if nxt is _SENTINEL_SDK_DONE:
                        continue
                    yield nxt
                return
            if isinstance(event, _SdkError):
                raise event.exc
            yield event
    finally:
        if not sdk_task.done():
            sdk_task.cancel()
            try:
                await sdk_task
            except BaseException:
                pass


class _SdkError:
    """内部包装：把 SDK 异常通过 queue 传给 merger."""
    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


# ────────────────────────────── 2.5.1 helpers ──────────────────────────────


def _collect_evidence_from_tool(evidence, tool_name: str, result) -> None:
    """把一条 ``tool_call_end`` 的 result 喂给 EvidenceIndex。

    仅处理知识库工具：``rag_retrieve`` / ``rag_read_doc`` / ``rag_graph_query``；
    其他工具（比如 ``spawn_subagent`` 的最终文本）不算 evidence 来源。
    """
    if not tool_name or result is None:
        return
    if tool_name == "rag_retrieve":
        evidence.add_from_rag_retrieve(result)
    elif tool_name == "rag_read_doc":
        evidence.add_from_rag_read_doc(result)
    elif tool_name == "rag_graph_query":
        evidence.add_from_rag_graph_query(result)
