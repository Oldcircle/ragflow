"""Agent v2 HTTP Blueprint.

URL 前缀 `/v1/agent_v2`（由 `api/apps/__init__.py:register_page` 自动注册）。
提供 Session CRUD、工具清单、以及 SSE 流式对话端点。

注意：`manager`、`app` 变量由加载器注入，不在本文件定义。
route 装饰器上加 `# noqa: F821` 抑制 linter 报错。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

from quart import Response, request

from api.apps import current_user, login_required
from api.db.services.agent_v2_service import (
    AgentV2MessageService,
    AgentV2SessionService,
    AgentV2ToolCallService,
)
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    get_request_json,
    server_error_response,
    validate_request,
)
from api.agent_v2.model_resolver import list_available_chat_models, resolve_model
from api.agent_v2.registry import ALL_TOOLS, list_tool_names
from api.agent_v2.runner import AgentRunner, ModelConfig
from api.agent_v2.templates import get_template, list_templates
from common.constants import RetCode

logger = logging.getLogger("ragflow.agent_v2.app")


# ────────────────────────────────────── Helpers ──────────────────────────────────────


def _build_model_config(conf: dict | None, tenant_id: str) -> ModelConfig:
    """根据 session 的 model_config_json + TenantLLM + env 构造 ModelConfig。

    见 `api/agent_v2/model_resolver.py` 的优先级规则。
    """
    resolved = resolve_model(conf, tenant_id)
    logger.info(
        "model resolved: %s (source=%s) for tenant=%s",
        resolved.display_name,
        resolved.source,
        tenant_id,
    )
    return resolved.config


def _session_dict(session) -> dict:
    """peewee model → JSON-safe dict。"""
    if isinstance(session, dict):
        return session
    return session.to_human_model_dict()


async def _persist_events(session_id: str, assistant_msg_id: str, events: list[dict]):
    """把一轮 Agent 事件流落库：assistant 消息 + 工具调用记录。"""
    # 聚合 assistant 文本 + thinking
    text_buf: list[str] = []
    think_buf: list[str] = []
    tool_call_ids: list[str] = []
    usage: dict = {}
    tool_starts: dict[str, dict] = {}  # id → {name, args}

    for ev in events:
        t = ev["type"]
        d = ev["data"]
        if t == "text_delta":
            text_buf.append(d.get("text", ""))
        elif t == "thinking":
            think_buf.append(d.get("text", ""))
        elif t == "tool_call_start":
            tool_call_ids.append(d["id"])
            tool_starts[d["id"]] = {"name": d["name"], "args": d.get("args", {})}
        elif t == "tool_call_end":
            # 先落 start 记录，再 end
            start = tool_starts.pop(d["id"], None)
            if start:
                AgentV2ToolCallService.record_start(
                    tool_use_id=d["id"],
                    session_id=session_id,
                    message_id=assistant_msg_id,
                    tool_name=start["name"],
                    args=start["args"],
                )
            AgentV2ToolCallService.record_end(
                tool_use_id=d["id"],
                result=d.get("result"),
                error=d.get("error"),
                duration_ms=d.get("duration_ms"),
            )
        elif t == "end":
            usage = d.get("usage") or {}

    # 登记 assistant 消息
    AgentV2MessageService.append(
        session_id=session_id,
        role="assistant",
        content="".join(text_buf),
        thinking="".join(think_buf),
        tool_call_ids=tool_call_ids,
        usage=usage,
        message_id=assistant_msg_id,
    )


# ────────────────────────────────────── Session CRUD ──────────────────────────────────────


@manager.route("/session", methods=["POST"])  # noqa: F821
@login_required
@validate_request("kb_ids")
async def create_session():
    try:
        req = await get_request_json()
        kb_ids = req["kb_ids"]

        # Phase 2.1：创建 session 前批量校验用户对 kb_ids 的访问权限。
        # 至少需要 VIEWER 才能用知识库做检索。
        from api.db.services.audit_log_service import AuditLogService
        from api.db.services.dataset_access_service import (
            DatasetAccessService,
            DatasetRole,
        )

        denied = [
            k for k in kb_ids
            if not DatasetAccessService.has_at_least(k, current_user.id, DatasetRole.VIEWER)
        ]
        if denied:
            for k in denied:
                AuditLogService.deny(
                    user_id=current_user.id,
                    tenant_id=current_user.id,
                    action="agent_v2.create_session",
                    resource_type="knowledgebase",
                    resource_id=k,
                    reason="no_viewer_access",
                    request=request,
                )
            return get_json_result(
                code=RetCode.AUTHENTICATION_ERROR,
                message=f"no access to dataset(s): {denied}",
            )

        session = AgentV2SessionService.create_session(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=req.get("name") or "Untitled Agent",
            kb_ids=kb_ids,
            system_prompt=req.get("system_prompt", ""),
            tool_names=req.get("tool_names"),
            model_config=req.get("model_config"),
            max_turns=int(req.get("max_turns", 20)),
            max_budget_usd=req.get("max_budget_usd", 1.0),
            citation_enforce_level=req.get("citation_enforce_level", "warn"),
            citation_numeric_strict=bool(req.get("citation_numeric_strict", True)),
            history_turn_limit=int(req.get("history_turn_limit", 10)),
        )
        AuditLogService.allow(
            user_id=current_user.id,
            tenant_id=current_user.id,
            action="agent_v2.create_session",
            resource_type="agent_v2_session",
            resource_id=session.id,
            metadata={"kb_ids": kb_ids},
            request=request,
        )
        return get_json_result(data=_session_dict(session))
    except ValueError as e:
        return get_data_error_result(message=str(e))
    except Exception as e:
        return server_error_response(e)


@manager.route("/session", methods=["GET"])  # noqa: F821
@login_required
async def list_sessions():
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 50))
        status = request.args.get("status", "active")
        rows = AgentV2SessionService.list_by_tenant(
            tenant_id=current_user.id,
            user_id=current_user.id,
            status=status,
            page=page,
            page_size=page_size,
        )
        return get_json_result(data={"sessions": rows})
    except Exception as e:
        return server_error_response(e)


@manager.route("/session/<session_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_session(session_id: str):
    try:
        session = AgentV2SessionService.get_by_id(session_id)
        if not session or session.tenant_id != current_user.id:
            return get_data_error_result(message="session not found")
        messages = AgentV2MessageService.list_by_session(session_id)
        tool_calls = AgentV2ToolCallService.list_by_session(session_id)
        return get_json_result(
            data={
                "session": _session_dict(session),
                "messages": messages,
                "tool_calls": tool_calls,
            }
        )
    except Exception as e:
        return server_error_response(e)


@manager.route("/session/<session_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_session(session_id: str):
    try:
        session = AgentV2SessionService.get_by_id(session_id)
        if not session or session.tenant_id != current_user.id:
            return get_data_error_result(message="session not found")
        AgentV2SessionService.soft_delete(session_id)
        return get_json_result(data={"deleted": True})
    except Exception as e:
        return server_error_response(e)


# ────────────────────────────────────── Subagent traces (P2.3) ──────────────────────────────────────


@manager.route("/session/<session_id>/subagent", methods=["GET"])  # noqa: F821
@login_required
async def list_subagent_traces(session_id: str):
    """列出一个 session 派出过的所有子 Agent 轨迹（按 start_time 升序）."""
    try:
        session = AgentV2SessionService.get_by_id(session_id)
        if not session or session.tenant_id != current_user.id:
            return get_data_error_result(message="session not found")
        from api.db.services.subagent_trace_service import SubagentTraceService
        rows = SubagentTraceService.list_by_session(session_id)
        return get_json_result(data={
            "traces": [
                {
                    "id": r.id,
                    "parent_tool_call_id": r.parent_tool_call_id,
                    "description": r.description,
                    "prompt": r.prompt,
                    "allowed_tools": list(r.allowed_tools or []),
                    "max_turns": r.max_turns,
                    "max_budget_usd": r.max_budget_usd,
                    "status": r.status,
                    "result_preview": r.result_preview,
                    "error": r.error,
                    "token_usage_json": r.token_usage_json,
                    "cost_usd": r.cost_usd,
                    "duration_ms": r.duration_ms,
                    "start_time": r.start_time,
                    "end_time": r.end_time,
                }
                for r in rows
            ],
        })
    except Exception as e:
        return server_error_response(e)


# ────────────────────────────────────── Tools info ──────────────────────────────────────


@manager.route("/template", methods=["GET"])  # noqa: F821
@login_required
async def list_agent_templates():
    """列出所有预置 Agent 模板，供 NewSessionDialog 一键选用。"""
    try:
        return get_json_result(data={"templates": list_templates()})
    except Exception as e:
        return server_error_response(e)


@manager.route("/definition", methods=["GET"])  # noqa: F821
@login_required
async def list_agent_definitions():
    """Phase 2.5.3 — 列出所有已注册的 AgentDefinition。

    query:
      - kind: "supervisor" | "subagent" | 不传=全部
    """
    try:
        from api.agent_v2.definitions import list_definitions

        kind = request.args.get("kind") or None
        defs = list_definitions(kind=kind)
        return get_json_result(data={"definitions": [d.to_dict() for d in defs]})
    except Exception as e:
        return server_error_response(e)


@manager.route("/model", methods=["GET"])  # noqa: F821
@login_required
async def list_available_models():
    """列出当前 tenant 配置过的 Chat 模型，供 NewSessionDialog 下拉用。"""
    try:
        models = list_available_chat_models(current_user.id)
        return get_json_result(data={"models": models})
    except Exception as e:
        return server_error_response(e)


@manager.route("/tool", methods=["GET"])  # noqa: F821
@login_required
async def list_agent_tools():
    try:
        tools_info = []
        for short_name, t in ALL_TOOLS.items():
            tools_info.append(
                {
                    "name": short_name,
                    "mcp_name": f"mcp__ragflow__{short_name}",
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
            )
        return get_json_result(
            data={"tools": tools_info, "mcp_tool_names": list_tool_names()}
        )
    except Exception as e:
        return server_error_response(e)


# ────────────────────────────────────── Conversation (SSE) ──────────────────────────────────────


@manager.route("/conversation", methods=["POST"])  # noqa: F821
@login_required
@validate_request("session_id", "message")
async def send_message():
    """流式发一条消息给 Agent，SSE 返回事件流。"""
    req = await get_request_json()
    session_id = req["session_id"]
    user_message = req["message"]

    session = AgentV2SessionService.get_by_id(session_id)
    if not session or session.tenant_id != current_user.id or session.status != "active":
        return get_data_error_result(message="session not found or inactive")

    # 登记 user 消息（保留 id 以便 2.5.2 拉 history 时排除本条）
    user_msg = AgentV2MessageService.append(
        session_id=session_id, role="user", content=user_message
    )
    user_msg_id = getattr(user_msg, "id", None)

    try:
        model_cfg = _build_model_config(session.model_config_json, session.tenant_id)
    except ValueError as e:
        return get_data_error_result(message=str(e))
    if not model_cfg.auth_token:
        return get_data_error_result(
            message="Model auth token missing. Configure a Chat model in Model Providers "
            "or set AGENT_V2_DEEPSEEK_KEY / AGENT_V2_ANTHROPIC_KEY env var."
        )

    # 生成稳定的 assistant msg id（供事件流和落库共用）
    from common.misc_utils import get_uuid

    assistant_msg_id = get_uuid()

    # Phase 2.5.2 — 拉历史消息 + compact summary（如果有）
    history_turn_limit = int(getattr(session, "history_turn_limit", None) or 10)
    summary_text = getattr(session, "summary_text", None) or ""
    summary_until_seq = int(getattr(session, "summary_until_seq", None) or 0)

    # 按 create_time 过滤掉已总结的旧消息
    since_ct: int | None = None
    if summary_until_seq > 0:
        # 第 summary_until_seq 条消息的 create_time（含）之前的都已并入 summary
        all_msgs = AgentV2MessageService.list_by_session(session.id)
        if 0 < summary_until_seq <= len(all_msgs):
            since_ct = int(all_msgs[summary_until_seq - 1].get("create_time") or 0)

    history = AgentV2MessageService.list_for_runner(
        session_id=session.id,
        limit=max(2, history_turn_limit * 2),  # 每轮 user+assistant，所以 ×2
        exclude_message_id=user_msg_id,
        since_create_time=since_ct,
    )

    runner = AgentRunner(
        tenant_id=session.tenant_id,
        kb_ids=list(session.kb_ids or []),
        system_prompt=session.system_prompt or "",
        model=model_cfg,
        tool_names=list(session.tool_names) if session.tool_names else None,
        user_id=session.user_id,
        max_turns=session.max_turns,
        max_budget_usd=session.max_budget_usd,
        session_id=session.id,  # Phase 2.3: 让 spawn_subagent 能引用父 session
        citation_enforce_level=session.citation_enforce_level or "warn",
        citation_numeric_strict=bool(session.citation_numeric_strict),
    )

    async def stream():
        events: list[dict] = []
        try:
            async for ev in runner.run(
                user_message,
                history=history,
                summary_text=summary_text,
            ):
                d = ev.to_dict()
                events.append(d)
                # 给 tool_call_start 立即落库（pending 状态）便于前端看到
                if d["type"] == "tool_call_start":
                    with contextlib.suppress(Exception):
                        AgentV2ToolCallService.record_start(
                            tool_use_id=d["data"]["id"],
                            session_id=session_id,
                            message_id=assistant_msg_id,
                            tool_name=d["data"]["name"],
                            args=d["data"].get("args", {}),
                        )
                elif d["type"] == "tool_call_end":
                    with contextlib.suppress(Exception):
                        AgentV2ToolCallService.record_end(
                            tool_use_id=d["data"]["id"],
                            result=d["data"].get("result"),
                            error=d["data"].get("error"),
                            duration_ms=d["data"].get("duration_ms"),
                        )
                yield "data: " + json.dumps(d, ensure_ascii=False) + "\n\n"
        except asyncio.CancelledError:
            logger.info("conversation stream cancelled for session %s", session_id)
            raise
        finally:
            # 收流后落 assistant 消息（即便失败也留最后状态）
            text = "".join(
                e["data"].get("text", "")
                for e in events
                if e["type"] == "text_delta"
            )
            thinking = "".join(
                e["data"].get("text", "")
                for e in events
                if e["type"] == "thinking"
            )
            tool_ids = [
                e["data"]["id"]
                for e in events
                if e["type"] == "tool_call_start"
            ]
            usage = next(
                (e["data"].get("usage", {}) for e in events if e["type"] == "end"),
                {},
            )
            with contextlib.suppress(Exception):
                AgentV2MessageService.append(
                    session_id=session_id,
                    role="assistant",
                    content=text,
                    thinking=thinking,
                    tool_call_ids=tool_ids,
                    usage=usage,
                    message_id=assistant_msg_id,
                )

            # Phase 3.1b — 记录 token + cost 用量（尽量不阻塞，静默失败）
            with contextlib.suppress(Exception):
                from api.db.services.tenant_quota_service import (
                    TenantUsageService,
                )
                subagent_spawns = sum(
                    1 for e in events if e["type"] == "subagent_start"
                )
                TenantUsageService.increment(
                    session.tenant_id,
                    token_in=int(usage.get("input_tokens") or 0),
                    token_out=int(usage.get("output_tokens") or 0),
                    cost_usd=float(usage.get("total_cost_usd") or 0.0),
                    subagent_spawns=subagent_spawns,
                )

            # Phase 2.5.2 — 触发 compact（fire-and-forget，不阻塞 SSE 收尾）
            with contextlib.suppress(Exception):
                from api.agent_v2.compactor import maybe_compact_session

                asyncio.create_task(
                    maybe_compact_session(
                        session_id=session_id,
                        model=model_cfg.model,
                        base_url=model_cfg.base_url,
                        auth_token=model_cfg.auth_token or "",
                    )
                )

    resp = Response(stream(), mimetype="text/event-stream")
    resp.headers.add_header("Cache-Control", "no-cache")
    resp.headers.add_header("X-Accel-Buffering", "no")
    return resp
