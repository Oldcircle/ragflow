"""AgentRunner — Claude Agent SDK 的适配层。

把 RAGFlow 的 tenant/kb 上下文喂给 SDK，
把 SDK 的异步消息流翻译成统一 Event 序列。
"""

from __future__ import annotations

import asyncio
import json
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
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
# TextBlock / ThinkingBlock 以前用于 AssistantMessage 路径；
# 切到 include_partial_messages=True 后只靠 StreamEvent 流文本，本文件不再直接引用。

from . import event as ev
from .errors import AgentError
from .registry import MCP_SERVER_NAME, build_mcp_server, list_tool_names
from .tools import _names as tool_names
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
        pending_plan_status: str | None = None,
        pending_plan_id: str | None = None,
        attachments: tuple = (),
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

        # Phase 2.6 v0.4 — plan gate runtime state
        self.pending_plan_status = pending_plan_status
        self.pending_plan_id = pending_plan_id

        # Phase 2.7 — attachments snapshot (tuple[AttachmentInfo, ...]).
        # Populated by the HTTP layer right before run(); None-safe.
        self.attachments = tuple(attachments or ())

        # 子发事件会 emit 到这个 queue，父在 run() loop 里把它们穿插进自己的 SDK 流
        self._event_bus: asyncio.Queue[ev.Event] | None = None

        # Phase 2.7 v0.20 — cancellation. Lazy-init in run() so the Event
        # is bound to the right loop. ``cancel()`` can be called from the
        # same loop (e.g. SSE stream consumer detecting client disconnect)
        # or via ``loop.call_soon_threadsafe`` from a different thread
        # (e.g. an HTTP cancel endpoint handler).
        self._cancel_event: asyncio.Event | None = None

    def cancel(self) -> None:
        """Signal the running tools to abort cooperatively.

        Idempotent — safe to call multiple times. The flag persists, so
        calling cancel() before ``run()`` makes the run abort on its
        first cancel-aware boundary. Tools using ``check_cancelled()``
        from ``api.agent_v2.tools.base`` will raise ``CancelledByCaller``;
        tools using ``is_cancelled()`` make their own bail decision.

        Note: this is cooperative — synchronous tools that don't poll
        will run to completion. For hard kill (e.g. SDK-CLI subprocess
        stuck in I/O), the caller should also cancel the asyncio task
        wrapping ``run()``; ``CancelledError`` propagates and most
        ``httpx`` calls handle it natively.
        """
        if self._cancel_event is None:
            # Pre-run cancellation — create the event eagerly so the next
            # run() inherits it instead of starting fresh.
            self._cancel_event = asyncio.Event()
        self._cancel_event.set()

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

        # Phase 2.6 v0.8 — 在 session.system_prompt 末尾显式追加"可用工具"段。
        # 抄 Claude Code 的 getUsingYourToolsSection 做法（src/constants/prompts.
        # ts:271-316）——**不只**靠 tool_use API 的 tools 参数告诉模型有什么工具，
        # 还在 system prompt 里显式枚举，外加**负面枚举**防 DeepSeek 从训练记忆
        # 里捞 Gmail/Drive/LSP/Skill/Bash 等 Claude 产品线工具幻觉出来。
        # lang 基于 system_prompt 含中英文比例粗判（保障房模板是中文老 prompt，
        # 研究 / 法务等 Phase 2.5+ 模板是英文新 prompt）。
        from .attachments import render_attachments_prompt_section
        from .prompting import render_tool_availability_section

        lang = _guess_prompt_lang(self.system_prompt)
        tool_section = render_tool_availability_section(
            self.tool_names, lang=lang
        )

        # Phase 2.7 — 附件段（仅在 session 有 staged/archived 附件时渲染）。
        # 对齐 claude-code-ref `getPlanModeAttachments` 的思路：per-turn 一次
        # 动态注入，保持段落结构稳定、工具清单不随 attachment 变动。
        attachments_section = render_attachments_prompt_section(
            self.attachments, lang=lang
        )

        pieces = [self.system_prompt.rstrip() if self.system_prompt else ""]
        if tool_section:
            pieces.append("\n---\n\n" + tool_section)
        if attachments_section:
            pieces.append("\n---\n" + attachments_section)
        full_system_prompt = "".join(pieces)

        # Capture SDK-CLI subprocess stderr into our logger. The SDK wraps
        # subprocess crashes in a generic "Command failed / Check stderr
        # output for details" message; without this callback, the "details"
        # are just dropped. Common signal: HTTP 402 from upstream means the
        # billing key has zero balance (DeepSeek is the most common offender
        # for our local dev setup).
        sdk_logger = logging.getLogger("ragflow.agent_v2.runner.sdk_cli")

        def _sdk_stderr_callback(line: str) -> None:
            line = line.rstrip()
            if line:
                sdk_logger.warning("[sdk-cli] %s", line)

        return ClaudeAgentOptions(
            model=self.model.model,
            fallback_model=self.model.fallback_model,
            system_prompt=full_system_prompt,
            mcp_servers={MCP_SERVER_NAME: mcp_server},
            allowed_tools=allowed,
            stderr=_sdk_stderr_callback,
            # **硬禁** Claude Code SDK 的所有内建工具 —— KB Agent 只能用我们 MCP
            # 里暴露的工具，绝不允许触达宿主 FS / 启动 shell / 联网抓站 / 用
            # SDK 自己的 Agent 机制绕开我们的 spawn_subagent。实测 A1/A2 里 LLM
            # 尝试过 Read / Agent / LS，这里一次性黑掉 Anthropic 文档列出的所有
            # Claude Code 内建工具名。
            disallowed_tools=[
                # 文件 / 编辑
                "Read", "Write", "Edit", "NotebookEdit",
                "LS",  # 目录列出
                # shell
                "Bash", "BashOutput", "KillShell", "KillBash",
                # 搜索
                "Glob", "Grep",
                # 联网
                "WebFetch", "WebSearch",
                # 任务 / 代理 / MCP 基建（用我们自己的 spawn_subagent 代替）
                "TodoWrite", "Task", "Agent",
                "ExitPlanMode",  # 我们用 submit_plan
                "SlashCommand",
                # v0.6-fix: 实测 supervisor 在真机里调过 ScheduleWakeup 去"自我唤醒
                # 60 秒后检查结果"——这是 Claude Code 的自调度能力，在 KB agent
                # 场景里没意义（agent 的 turn 在用户下一条消息之前不会恢复），而且
                # 让 agent 产生"时间在流动"的幻觉。一起禁掉。
                "ScheduleWakeup",
                "CronCreate", "CronList", "CronDelete",
                # ToolSearch 和 deferred tool 机制在 KB agent 场景不适用
                "ToolSearch",
                # Plan mode 相关残余
                "EnterPlanMode", "EnterWorktree", "ExitWorktree",
                # 远程触发 / 推送通知类
                "RemoteTrigger", "PushNotification", "Monitor",
                # Task 管理（v0.6 agent 用自己的 [step K/N] 标记，不走 SDK task）
                "TaskCreate", "TaskList", "TaskUpdate", "TaskGet",
                "TaskStop", "TaskOutput",
                # AskUserQuestion 的 SDK 原生工具（我们用自己的 ask_user_question）
                "AskUserQuestion",
            ],
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            permission_mode=self.permission_mode,  # type: ignore[arg-type]
            env=env,
            # 打开低级 Anthropic stream events（content_block_delta 等），
            # 这样 _translate 能把 token-by-token 的增量通过 text_delta 发给前端，
            # 否则前端只能在 AssistantMessage 结束一整段后才看到完整文本，
            # 表现为"一次性出现"而不是流式。
            include_partial_messages=True,
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

        # Bind the cancel event to this loop. Reuse if cancel() was called
        # before run() (preserves the signal); otherwise create fresh.
        if self._cancel_event is None:
            self._cancel_event = asyncio.Event()

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
            pending_plan_status=self.pending_plan_status,
            pending_plan_id=self.pending_plan_id,
            plan_submitted_this_turn=False,
            attachments=self.attachments,
            cancelled=self._cancel_event,
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
        # Phase 2.6 v0.8.3 — 连续空 rag 结果计数，≥ _EMPTY_RAG_THRESHOLD 时
        # 强制收尾，防止 Q13 类"诱导编造" scenario 里 Agent 无限换关键词后
        # subprocess crash。
        consecutive_empty_rag = 0
        force_stopped = False

        try:
            async for event in _merge_streams(sdk_iter(), self._event_bus):
                # ── intercept text_delta + tool_call_* for the validator ──
                if event.type == "text_delta":
                    text_parts.append(event.data.get("text") or "")
                    # 正常有文本产出，重置空检索计数（Agent 在正常推理而非死循环）
                    consecutive_empty_rag = 0
                elif event.type == "tool_call_start":
                    tid = event.data.get("id")
                    tname = event.data.get("name") or ""
                    if tid:
                        tool_name_by_id[tid] = tname
                elif event.type == "tool_call_end":
                    tid = event.data.get("id")
                    tname = tool_name_by_id.get(tid, "")
                    result = event.data.get("result")
                    _collect_evidence_from_tool(evidence, tname, result)

                    # Phase 2.8.3 — Interactive Tool Pause Framework：交互工具
                    # （ask_user_question / submit_plan）在 result 顶层设
                    # ``pause_loop=true`` 标记，让 runner 立刻 force-stop SDK loop。
                    #
                    # 不靠 prompt 嘱托模型 STOP（软约束、易漂移），而是程序硬
                    # 拦截 —— 模型根本没有机会拿到 tool_result 后继续生成文本。
                    # 这是 Claude Code AskUserQuestionTool 的 ``shouldDefer=true``
                    # + ``checkPermissions: 'ask'`` 在 HTTP-SSE 约束下的等价
                    # 实现（详见 STATUS Phase 2.8.3 段）。
                    if _has_pause_loop_marker(result):
                        # 先 yield tool_call_end 让前端把卡片 + 工具状态画出来
                        yield event
                        logger.info(
                            "Runner.run: pause_loop marker on %s — force-stopping turn",
                            tname,
                        )
                        force_stopped = True
                        # pause 路径下不跑 citation validator：那一轮没产出文本，
                        # 只有交互卡片，没什么可校验的（且本轮 evidence 大概率
                        # 已经收集到了，留给恢复后的 turn 校验）。
                        yield ev.end()
                        return

                    # 空检索计数 — 只对 rag_* 读工具计；其它工具（doc_ops / web_* /
                    # spawn_subagent / ask_user_question 等）不影响。
                    if _is_empty_rag_result(tname, result):
                        consecutive_empty_rag += 1
                        logger.debug(
                            "empty rag result #%d (tool=%s)",
                            consecutive_empty_rag,
                            tname,
                        )
                        if consecutive_empty_rag >= _EMPTY_RAG_THRESHOLD:
                            # Yield current tool_call_end first so UI shows the
                            # last call ran to completion, then inject our
                            # concede text + clean end.
                            yield event
                            logger.warning(
                                "Runner.run: forcing graceful stop after "
                                "%d consecutive empty rag results "
                                "(Q13-class scenario)",
                                consecutive_empty_rag,
                            )
                            concede_text = (
                                "\n\n_(本知识库未查到相关内容。"
                                "已连续多次检索无果——很可能该信息不在本 KB 范围内。"
                                "若是具体文件号 / 条款号，请核对后再问；"
                                "或尝试用主题关键词换个角度提问。)_"
                            )
                            text_parts.append(concede_text)
                            yield ev.text_delta(text=concede_text)
                            force_stopped = True
                            # Still run citation validator on the combined
                            # text before emitting end
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
                            yield ev.end()
                            return
                    else:
                        # 非空结果 → 重置计数（哪怕只有 1 个低分 chunk 也算有进展）
                        consecutive_empty_rag = 0

                if event.type == "end" and not force_stopped:
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
        """把 SDK 消息翻译成统一 Event。

        使用 ``include_partial_messages=True`` 后，文本/思考靠 ``StreamEvent``
        逐 token 流出；``AssistantMessage`` 里携带的是完整文本 **回放**，
        如果再 yield 一次，前端就会拼到已经流出的 text 后面（= 文本翻倍）。
        所以 AssistantMessage 路径只保留工具调用的 start（工具 id / input
        是在 block 结束后才完整确定的）。
        """
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    tool_call_starts[block.id] = time.time()
                    # 让 spawn_subagent 等工具知道自己是哪个 tool_use
                    if ctx is not None:
                        ctx.current_tool_call_id = block.id
                    if tool_current_holder is not None:
                        tool_current_holder["id"] = block.id
                    yield ev.tool_call_start(
                        tool_id=block.id, name=block.name, args=block.input
                    )
                # TextBlock / ThinkingBlock 已由 StreamEvent 路径流式发出，
                # 不再在此处重复 yield，避免文本翻倍。
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
            # 低级 Anthropic 流事件：content_block_delta 才带文本增量。
            # 我们只转发 text_delta / thinking_delta；tool_use 的 input 边流边改
            # 不适合提前暴露给前端 UI，等 AssistantMessage 一次给齐更稳。
            raw = getattr(msg, "event", None) or {}
            etype = raw.get("type")
            if etype == "content_block_delta":
                delta = raw.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    text = delta.get("text") or ""
                    if text:
                        yield ev.text_delta(text)
                elif dtype == "thinking_delta":
                    thinking = delta.get("thinking") or ""
                    if thinking:
                        yield ev.thinking(thinking)
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
                # Phase 2.6 v0.5 — render tool calls that the assistant ran
                # as part of this history entry. Claude Code keeps the full
                # tool_use/tool_result blocks in history; the SDK's
                # query(prompt=...) interface only accepts a string, so we
                # render a compact text version into <conversation-history>.
                if role == "assistant":
                    tool_calls = m.get("tool_calls") or []
                    tool_lines = _format_tool_calls_for_history(tool_calls)
                else:
                    tool_lines = []
                if not content and not tool_lines:
                    continue
                # 对 assistant 消息保留但截断——太长的历史回答对追问不相关的细节
                # 反而是噪音，压到 1200 字内够识别 topic + 引用编号
                if role == "assistant" and len(content) > 1200:
                    content = content[:1200] + " …（已截断）"
                lines.append(f"[#{idx}] {role}: {content or '(tool activity only)'}")
                for tl in tool_lines:
                    lines.append(f"      {tl}")
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

        Phase 2.8.2 — citation_without_evidence 走专门分支：
          - 不论 enforce 模式都先打审计（``agent_v2.citation_phantom``，便于
            dashboard 聚合 prompt drift 指标，对齐 claude-code-ref 的
            ``logEvent`` 全程审计模式）
          - strict 模式走 ``rewrite_to_no_basis`` 重写为无出处免责文案
            （而非 ``rewrite_answer_strict`` —— 后者要求 evidence ≥ 1 才能
            修引用，无 evidence 时只会返 None）
        """
        from .validators import validate_citations
        from .validators.rewrite import rewrite_answer_strict, rewrite_to_no_basis

        # Phase 2.8.2 — citation_without_evidence 专用分支
        phantom_issue = next(
            (i for i in issues if i.kind == "citation_without_evidence"),
            None,
        )
        if phantom_issue is not None:
            self._audit_citation_phantom(final_text=final_text)
            async for e in self._handle_phantom_citations(
                final_text=final_text,
                phantom_issue=phantom_issue,
                rewriter=rewrite_to_no_basis,
            ):
                yield e
            return

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

    # ──────────────────  Phase 2.8.2 — phantom citations  ──────────────────

    def _audit_citation_phantom(self, *, final_text: str) -> None:
        """记录"无证据却带 [N]"事件到 access_audit_log，便于运维 dashboard。

        参照 claude-code-ref 在每个关键决策点都 ``logEvent`` 的模式。审计写入
        失败绝不阻塞业务流程（``AuditLogService.log`` 自身已 try/except 兜底）。
        """
        try:
            from api.db.services.audit_log_service import AuditLogService

            AuditLogService.log(
                user_id=self.user_id,
                tenant_id=self.tenant_id or "",
                action="agent_v2.citation_phantom",
                resource_type="agent_v2_session",
                resource_id=self.session_id,
                result="deny",  # deny = 校验未通过
                reason="answer_cited_without_retrieval",
                metadata={
                    "answer_chars": len(final_text or ""),
                    "model": self.model.model if self.model else None,
                },
            )
        except Exception:
            logger.exception("citation_phantom audit log failed")

    async def _handle_phantom_citations(
        self,
        *,
        final_text: str,
        phantom_issue,
        rewriter,
    ) -> AsyncIterator[ev.Event]:
        """处理 ``citation_without_evidence`` 单一 issue 的事件序列。

        - warn 模式：发一条 ``citation_warning`` level=warn，**不**重写。
          前端按新 kind 渲染（红色"未检索却带引用"标签）。
        - strict 模式：调 ``rewrite_to_no_basis`` 把答复降级为无出处免责
          文案，**追加**到原答复后（而非替换），让用户能对比看到原始
          幻觉版 + 校正版。

        Args:
            rewriter: ``rewrite_to_no_basis`` 函数；通过参数注入便于测试 mock。
        """
        issues_dict = [phantom_issue.to_dict()]

        if self.citation_enforce_level != "strict":
            yield ev.citation_warning(issues=issues_dict, level="warn")
            return

        # strict：尝试 no-basis 重写
        rewritten = await rewriter(
            original_text=final_text,
            citation_count=_count_citations(final_text),
            fallback_hint=None,  # 未来可从 supervisor 配置读
            model=self.model.model,
            base_url=self.model.base_url,
            auth_token=self.model.auth_token or "",
        )

        if rewritten:
            banner = (
                "\n\n---\n"
                "**🛡️ 校正答复（strict 模式：检测到无检索却带引用，已自动改写"
                "为免责声明）**\n\n"
            )
            yield ev.text_delta(banner + rewritten + "\n")
            yield ev.citation_warning(issues=issues_dict, level="strict_rewritten")
            return

        # 重写失败：硬编码 fallback
        fallback = (
            "\n\n---\n"
            "**⚠️ 系统提示**：本次答复带有 [N] 引用，但实际并未从知识库检索到"
            "任何片段（strict 模式自动校验）。前述内容**未经知识库核实**，"
            "建议以官方政策原文或向相应部门咨询为准。\n"
        )
        yield ev.text_delta(fallback)
        yield ev.citation_warning(issues=issues_dict, level="strict_failed")

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


def _count_citations(text: str) -> int:
    """Count [N] markers in ``text`` — used to brief ``rewrite_to_no_basis``.

    Lightweight helper to avoid pulling :class:`CitationExtractor` into the
    runner module just for one int.
    """
    if not text:
        return 0
    from .validators.citation import CitationExtractor

    return len(CitationExtractor.extract_citations(text))


_TOOL_CALL_ARG_CHARS = 180
_TOOL_CALL_RESULT_CHARS = 320
_TOOL_CALL_MAX_PER_TURN = 6


def _format_tool_calls_for_history(tool_calls: list[dict]) -> list[str]:
    """Render a compact ``[tool] name(args) → result`` text line per call.

    Phase 2.6 v0.5 — called by ``_build_prompt_with_history`` when the
    history dict carries ``tool_calls``. Keeps size tight: args and result
    truncated to ~200/320 chars each, at most 6 calls per turn (beyond that,
    replaced by a ``… (k more)`` line). If the LLM wants the full
    result it can always re-call the tool.
    """
    if not tool_calls:
        return []
    import json

    lines: list[str] = []
    for i, t in enumerate(tool_calls[:_TOOL_CALL_MAX_PER_TURN]):
        name = t.get("tool_name") or "unknown"
        args = t.get("args") or {}
        if isinstance(args, (dict, list)):
            args_str = json.dumps(args, ensure_ascii=False, separators=(",", ":"), default=str)
        else:
            args_str = str(args)
        if len(args_str) > _TOOL_CALL_ARG_CHARS:
            args_str = args_str[:_TOOL_CALL_ARG_CHARS] + "…"

        status = (t.get("status") or "unknown").lower()
        if status == "error":
            outcome = f"ERROR: {str(t.get('error') or '')[:_TOOL_CALL_RESULT_CHARS]}"
        else:
            result = t.get("result")
            if result is None or result == "":
                outcome = "(no output)"
            else:
                if isinstance(result, (dict, list)):
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                else:
                    result_str = str(result)
                if len(result_str) > _TOOL_CALL_RESULT_CHARS:
                    result_str = result_str[:_TOOL_CALL_RESULT_CHARS] + "…"
                outcome = result_str
        duration = t.get("duration_ms") or 0
        lines.append(f"[tool] {name}({args_str}) → {outcome} ({duration}ms)")

    remaining = len(tool_calls) - _TOOL_CALL_MAX_PER_TURN
    if remaining > 0:
        lines.append(f"[tool] … {remaining} more call(s) omitted")
    return lines


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


# Phase 2.6 v0.8.3 — 连续空检索的硬上限（ref: claude-code-ref prompts.ts:235 +
# generalPurposeAgent.ts:13，那两处只说"multiple search strategies"而没定上
# 界，我们的 Q13 测试里 Agent 连 rag_retrieve / list_docs / read_doc 11 次
# 空结果后 subprocess crash，所以在 runner 层加硬 guard）。
#
# 阈值 3 是保守值：允许"正常的 KB 没查到 → 换关键词 → 再没查到 → 换第 3 个关
# 键词"这种合理试错，但拒绝无限循环。
_EMPTY_RAG_THRESHOLD = 3
_EMPTY_RAG_TOOLS = (
    tool_names.RAG_RETRIEVE,
    tool_names.RAG_LIST_DOCS,
    tool_names.RAG_READ_DOC,
    tool_names.RAG_GRAPH_QUERY,
)


def _has_pause_loop_marker(result) -> bool:
    """Phase 2.8.3 — observe ``pause_loop=true`` in a tool's JSON result.

    The marker is set by interactive tools via ``mcp_pause_response()``
    (see ``tools/base.py``) to request that the runner force-stop the SDK
    loop. We accept either a parsed dict or a raw JSON string — the
    surrounding code already passes both shapes through to other detectors
    like ``_is_empty_rag_result``.

    Defensive parsing: any exception → False (don't accidentally pause on
    a benign tool result that happens to fail to parse).
    """
    if result is None:
        return False
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(data, dict) and bool(data.get("pause_loop"))


def _is_empty_rag_result(tool_name: str, result) -> bool:
    """True if this is a rag_* tool that returned no useful content.

    `rag_retrieve` with 0 chunks or all `similarity < 0.2` → empty.
    `rag_list_docs` with 0 docs → empty.
    `rag_read_doc` with no content/chunks → empty.
    `rag_graph_query` with no entities/relations → empty.

    Unparseable / non-rag tools → False (don't count against threshold).
    """
    short = tool_name.rsplit("__", 1)[-1] if tool_name else ""
    if short not in _EMPTY_RAG_TOOLS:
        return False
    if result is None:
        return True
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    if short == tool_names.RAG_RETRIEVE:
        chunks = data.get("chunks") or []
        if not chunks:
            return True
        sims = [
            float(c.get("similarity") or 0)
            for c in chunks
            if isinstance(c, dict)
        ]
        return bool(sims) and max(sims) < 0.2
    if short == tool_names.RAG_LIST_DOCS:
        return not (data.get("docs") or [])
    if short == tool_names.RAG_READ_DOC:
        return not (data.get("chunks") or data.get("content"))
    if short == tool_names.RAG_GRAPH_QUERY:
        return not (data.get("entities") or data.get("relations"))
    return False


def _guess_prompt_lang(system_prompt: str | None) -> str:
    """Return 'zh' if the prompt is majority CJK, else 'en'.

    Used to pick which language variant of the tool-availability section to
    append. Templates shipping with Chinese ``_STRICT_RAG_PROMPT_TEMPLATE``
    (Phase 1, in ``templates.py``) should get the zh section so the whole
    system prompt reads consistently; English Phase-2.5+ definition prompts
    get the en variant.

    Heuristic: count CJK characters (U+4E00–U+9FFF main block). If they
    outnumber ASCII letters, treat as zh. ``None`` / empty falls back to en.
    """
    if not system_prompt:
        return "en"
    cjk = 0
    ascii_letters = 0
    for ch in system_prompt:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:
            cjk += 1
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            ascii_letters += 1
    return "zh" if cjk > ascii_letters else "en"


def _collect_evidence_from_tool(evidence, tool_name: str, result) -> None:
    """把一条 ``tool_call_end`` 的 result 喂给 EvidenceIndex。

    仅处理知识库工具：``rag_retrieve`` / ``rag_read_doc`` / ``rag_graph_query``；
    其他工具（比如 ``spawn_subagent`` 的最终文本）不算 evidence 来源。

    注意：Claude Agent SDK 在工具名前加 ``mcp__<server>__`` 前缀，
    所以这里用 ``endswith`` 匹配而非精确相等——否则 evidence 永远是空的，
    导致 citation validator 在「明明调了 rag_retrieve」时照样报
    ``only 0 chunks available`` 和 ``number_unsupported`` 的假阳性。
    """
    if not tool_name or result is None:
        return
    # 统一剥掉 SDK prefix
    short = tool_name.rsplit("__", 1)[-1]
    if short == "rag_retrieve":
        evidence.add_from_rag_retrieve(result)
    elif short == "rag_read_doc":
        evidence.add_from_rag_read_doc(result)
    elif short == "rag_graph_query":
        evidence.add_from_rag_graph_query(result)
