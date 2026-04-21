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
from api.agent_v2.registry import ALL_TOOLS, list_tool_names
from api.agent_v2.runner import AgentRunner, ModelConfig
from common.constants import RetCode

logger = logging.getLogger("ragflow.agent_v2.app")


# ────────────────────────────────────── Helpers ──────────────────────────────────────


def _build_model_config(conf: dict | None) -> ModelConfig:
    """根据 session 的 model_config_json 构造 ModelConfig。

    Phase 1 简化策略：
    - 若 conf 里有 `auth_token` 直接用
    - 否则按 provider 从环境变量取 key（`AGENT_V2_DEEPSEEK_KEY` / `AGENT_V2_ANTHROPIC_KEY`）
    Phase 3 再做 TenantLLM 集成。
    """
    conf = conf or {}
    model = conf.get("model") or "claude-sonnet-4-5"
    base_url = conf.get("base_url") or None
    token = conf.get("auth_token")

    if not token:
        if base_url and "deepseek" in (base_url or "").lower():
            token = os.environ.get("AGENT_V2_DEEPSEEK_KEY") or os.environ.get(
                "DEEPSEEK_API_KEY"
            )
        else:
            token = os.environ.get("AGENT_V2_ANTHROPIC_KEY") or os.environ.get(
                "ANTHROPIC_API_KEY"
            )

    return ModelConfig(
        model=model,
        base_url=base_url,
        auth_token=token,
        extra_env=conf.get("extra_env") or {},
    )


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
        session = AgentV2SessionService.create_session(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=req.get("name") or "Untitled Agent",
            kb_ids=req["kb_ids"],
            system_prompt=req.get("system_prompt", ""),
            tool_names=req.get("tool_names"),
            model_config=req.get("model_config"),
            max_turns=int(req.get("max_turns", 20)),
            max_budget_usd=req.get("max_budget_usd", 1.0),
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


# ────────────────────────────────────── Tools info ──────────────────────────────────────


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

    # 登记 user 消息
    AgentV2MessageService.append(
        session_id=session_id, role="user", content=user_message
    )

    model_cfg = _build_model_config(session.model_config_json)
    if not model_cfg.auth_token:
        return get_data_error_result(
            message="Model auth token not configured. Set AGENT_V2_DEEPSEEK_KEY or AGENT_V2_ANTHROPIC_KEY env var."
        )

    # 生成稳定的 assistant msg id（供事件流和落库共用）
    from common.misc_utils import get_uuid

    assistant_msg_id = get_uuid()

    runner = AgentRunner(
        tenant_id=session.tenant_id,
        kb_ids=list(session.kb_ids or []),
        system_prompt=session.system_prompt or "",
        model=model_cfg,
        tool_names=list(session.tool_names) if session.tool_names else None,
        user_id=session.user_id,
        max_turns=session.max_turns,
        max_budget_usd=session.max_budget_usd,
    )

    async def stream():
        events: list[dict] = []
        try:
            async for ev in runner.run(user_message):
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

    resp = Response(stream(), mimetype="text/event-stream")
    resp.headers.add_header("Cache-Control", "no-cache")
    resp.headers.add_header("X-Accel-Buffering", "no")
    return resp
