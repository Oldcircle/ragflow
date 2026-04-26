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

from quart import Response, request

from api.apps import current_user, login_required
from api.db.services.agent_v2_service import (
    AgentV2AttachmentService,
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
from api.agent_v2.plan_decision import (
    augment_for_plan_decision,
    augment_for_question_answer,
    parse_plan_decision,
    parse_question_answer,
)
from api.agent_v2.registry import ALL_TOOLS, list_tool_names
from api.agent_v2 import runner_registry
from api.agent_v2.runner import AgentRunner, ModelConfig
from api.agent_v2.templates import list_templates
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

        # Phase 2.6 v0.6-fix: enforce supervisor tool whitelist at session
        # creation. If the caller didn't specify tool_names (or passed a
        # falsy value like {}), fall back to SUPERVISOR_TOOLS so write tools
        # only reach the session via spawn_subagent → archivist/librarian.
        # Without this, writes like doc_tag / doc_reparse were callable by
        # the supervisor directly, bypassing the plan-gate architecture.
        requested_tools = req.get("tool_names")
        if isinstance(requested_tools, list) and requested_tools:
            effective_tool_names = requested_tools
        else:
            from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

            effective_tool_names = list(SUPERVISOR_TOOLS)

        session = AgentV2SessionService.create_session(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=req.get("name") or "Untitled Agent",
            kb_ids=kb_ids,
            system_prompt=req.get("system_prompt", ""),
            tool_names=effective_tool_names,
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


# Phase 2.8.1 — PATCH session settings.
#
# Whitelisted, conservative subset of fields users can mutate after session
# creation. Model and system_prompt are intentionally NOT here: cross-provider
# tool_use format incompatibility makes mid-conversation model switches
# fragile, and changing system_prompt mid-flow effectively swaps agent
# identity — both are best handled by creating a new session.
#
# When tool_names changes, the session prompt's "# Available Tools" section
# is now stale relative to the new toolset. Re-rendering the prompt requires
# knowing which AgentDefinition produced it, which the schema does not yet
# track (TODO v0.15: add ``definition_name`` column). For now we just flag
# the user and let them re-render via a new session if they care.
from api.agent_v2.session_patch import validate_patch_body  # noqa: E402


@manager.route("/session/<session_id>", methods=["PATCH"])  # noqa: F821
@login_required
async def patch_session(session_id: str):
    """Update editable session settings (whitelist in ``session_patch.py``)."""
    try:
        session = AgentV2SessionService.get_by_id(session_id)
        if not session or session.tenant_id != current_user.id:
            return get_data_error_result(message="session not found")

        body = await get_request_json() or {}
        if not isinstance(body, dict):
            return get_data_error_result(
                message="body must be a JSON object",
            )

        from api.db.services.dataset_access_service import DatasetAccessService

        def _accessible(ids: list[str]) -> list[str]:
            return DatasetAccessService.filter_accessible_kb_ids(
                ids, current_user.id
            )

        updates, error = validate_patch_body(
            body,
            valid_tool_names=set(ALL_TOOLS.keys()),
            filter_accessible_kbs=_accessible,
        )
        if error is not None:
            return get_data_error_result(message=error)

        AgentV2SessionService.update_fields(session_id, **updates)
        fresh = AgentV2SessionService.get_by_id(session_id)
        warnings = []
        if "tool_names" in updates:
            warnings.append(
                "Tool list changed but the cached system_prompt still "
                "references the old tool set. Create a new session to "
                "fully refresh the prompt."
            )
        return get_json_result(
            data={
                "session": _session_dict(fresh),
                "updated_fields": sorted(updates.keys()),
                "warnings": warnings,
            }
        )
    except Exception as e:
        return server_error_response(e)


@manager.route("/session/<session_id>/cancel", methods=["POST"])  # noqa: F821
@login_required
async def cancel_session_run(session_id: str):
    """Phase 2.7 v0.21 — abort the active runner for this session.

    Looks up the in-process runner registry and calls ``cancel()`` on
    the AgentRunner currently serving this session. Tools using
    ``check_cancelled()`` (web_search / web_fetch / etc.) will bail at
    their next cooperative check; the SSE stream then emits a
    ``cancelled`` error envelope and unwinds normally.

    Idempotent: calling cancel on a session that isn't running
    (no active turn, or running on a different pod) returns
    ``{"cancelled": false}`` without error. The frontend can call this
    safely on every "stop" click without coordinating with stream state.

    RBAC: a user can only cancel their own tenant's sessions. There's
    no separate per-session role for cancellation — if you can read the
    session, you can stop its run.
    """
    try:
        session = AgentV2SessionService.get_by_id(session_id)
        if not session or session.tenant_id != current_user.id:
            return get_data_error_result(message="session not found")
        cancelled = runner_registry.cancel(session_id)
        return get_json_result(data={
            "session_id": session_id,
            "cancelled": cancelled,
            "active_runs_in_process": runner_registry.active_session_count(),
        })
    except Exception as e:
        return server_error_response(e)


# ────────────────────────────────────── Attachments (Phase 2.7 Stage 1) ──────────────────────────────────────


def _attachment_dict(row) -> dict:
    """Serialize an AgentV2Attachment row for the HTTP client. Preview is
    intentionally truncated here even though the DB may hold up to ~8KB —
    the UI shows a collapsed chip, full preview is rendered on plan card."""
    return {
        "id": row.id,
        "session_id": row.session_id,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "hash_xxh128": row.hash_xxh128,
        "origin": row.origin,
        "source_url": row.source_url,
        "preview_text": row.preview_text,
        "status": row.status,
        "archived_doc_id": row.archived_doc_id,
        "archived_kb_id": row.archived_kb_id,
        "archived_at": row.archived_at,
        "expires_at": row.expires_at,
        "create_time": row.create_time,
    }


@manager.route("/session/<session_id>/attachments", methods=["POST"])  # noqa: F821
@login_required
async def upload_session_attachment(session_id: str):
    """Multipart upload one or more attachments to a session.

    Response shape::

        {"uploaded": [{attachment}, ...], "rejected": [{"filename": ..., "reason": ...}]}

    Dedupe semantics:
    - Same tenant + same xxh128 hash → returns the existing attachment (status
      staged or archived). Client sees the same row in ``uploaded``; the
      frontend chip doesn't distinguish new vs dedup.
    """
    from common import settings

    from api.agent_v2.attachments import (
        AGENT_V2_ATTACHMENT_BUCKET,
        MAX_ATTACHMENT_SIZE_BYTES,
        MAX_SESSION_STAGED_BYTES,
        MAX_SESSION_STAGED_COUNT,
        MIME_WHITELIST,
        extract_preview,
        hash_content,
        object_key,
        resolve_mime_type,
    )
    from api.db.services.audit_log_service import AuditLogService

    try:
        session = AgentV2SessionService.get_by_id(session_id)
        if not session or session.tenant_id != current_user.id:
            return get_data_error_result(message="session not found")

        files = await request.files
        file_list = files.getlist("file") if "file" in files else []
        if not file_list:
            return get_json_result(
                code=RetCode.ARGUMENT_ERROR,
                message="no file part — use multipart form field 'file'",
            )

        # Quota checks at session level
        existing_count = AgentV2AttachmentService.count_staged_for_session(session_id)
        if existing_count >= MAX_SESSION_STAGED_COUNT:
            return get_json_result(
                code=RetCode.ARGUMENT_ERROR,
                message=f"session already has {existing_count} staged attachments "
                f"(max {MAX_SESSION_STAGED_COUNT}); reject or archive some first",
            )
        existing_bytes = AgentV2AttachmentService.total_staged_bytes_for_session(session_id)

        uploaded: list[dict] = []
        rejected: list[dict] = []

        for file_obj in file_list:
            filename = (file_obj.filename or "").strip()
            if not filename:
                rejected.append({"filename": "", "reason": "empty_filename"})
                continue

            # Read fully (we need it for hash + preview + size check)
            blob = file_obj.read()
            size = len(blob)

            if size == 0:
                rejected.append({"filename": filename, "reason": "empty_file"})
                continue
            if size > MAX_ATTACHMENT_SIZE_BYTES:
                rejected.append({
                    "filename": filename,
                    "reason": f"too_large: {size} > {MAX_ATTACHMENT_SIZE_BYTES}",
                })
                continue
            if existing_bytes + size > MAX_SESSION_STAGED_BYTES:
                rejected.append({
                    "filename": filename,
                    "reason": f"session_quota_exceeded: "
                              f"{existing_bytes + size} > {MAX_SESSION_STAGED_BYTES}",
                })
                continue

            client_mime = (
                getattr(file_obj, "content_type", None)
                or getattr(file_obj, "mimetype", None)
                or ""
            )
            mime = resolve_mime_type(client_mime, filename)
            if not mime:
                rejected.append({
                    "filename": filename,
                    "reason": f"unsupported_mime: client={client_mime!r}; "
                              f"allowed={sorted(MIME_WHITELIST)}",
                })
                continue

            digest = hash_content(blob)

            # Dedupe per tenant+hash — staged or already archived both
            # qualify. Rejected/expired do NOT (we want a fresh row).
            existing = AgentV2AttachmentService.find_by_hash(
                tenant_id=current_user.id, hash_xxh128=digest,
            )
            if existing:
                uploaded.append(_attachment_dict(existing))
                existing_bytes += 0  # didn't actually consume new bytes
                continue

            # Write to MinIO first; DB insert is cheap and reversible
            # via reject/expire if blob upload succeeds.
            key = object_key(
                tenant_id=current_user.id,
                session_id=session_id,
                attachment_id=digest,  # temporary — real id after DB row
            )
            try:
                settings.STORAGE_IMPL.put(
                    AGENT_V2_ATTACHMENT_BUCKET, key, blob, current_user.id
                )
            except Exception as exc:
                logger.exception("attachment blob upload failed")
                rejected.append({
                    "filename": filename,
                    "reason": f"storage_error: {type(exc).__name__}: {exc}",
                })
                continue

            preview = extract_preview(blob, mime)

            row = AgentV2AttachmentService.create_staged(
                session_id=session_id,
                tenant_id=current_user.id,
                uploaded_by=current_user.id,
                filename=filename,
                mime_type=mime,
                size_bytes=size,
                hash_xxh128=digest,
                blob_path=f"{AGENT_V2_ATTACHMENT_BUCKET}/{key}",
                origin="upload",
                preview_text=preview,
            )
            uploaded.append(_attachment_dict(row))
            existing_bytes += size

            AuditLogService.allow(
                user_id=current_user.id,
                tenant_id=current_user.id,
                action="agent_v2.attachment_upload",
                resource_type="agent_v2_attachment",
                resource_id=row.id,
                metadata={
                    "session_id": session_id,
                    "filename": filename,
                    "mime": mime,
                    "size_bytes": size,
                },
                request=request,
            )

        return get_json_result(data={"uploaded": uploaded, "rejected": rejected})
    except Exception as e:
        return server_error_response(e)


@manager.route("/session/<session_id>/attachments", methods=["GET"])  # noqa: F821
@login_required
async def list_session_attachments(session_id: str):
    """List attachments on a session (default: staged + archived; add
    ``?include_rejected=1`` to include terminal states)."""
    try:
        session = AgentV2SessionService.get_by_id(session_id)
        if not session or session.tenant_id != current_user.id:
            return get_data_error_result(message="session not found")

        include_rejected = request.args.get("include_rejected", "").lower() in ("1", "true", "yes")
        statuses = (
            ("staged", "archived", "rejected", "expired")
            if include_rejected
            else ("staged", "archived")
        )
        rows = AgentV2AttachmentService.list_by_session(
            session_id=session_id, statuses=statuses,
        )
        return get_json_result(data={
            "attachments": [_attachment_dict(r) for r in rows],
        })
    except Exception as e:
        return server_error_response(e)


@manager.route("/session/<session_id>/attachments/<attachment_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def reject_session_attachment(session_id: str, attachment_id: str):
    """User-initiated rejection → status=rejected. Blob stays in MinIO for
    7 days audit; the cron sweeper (not wired in Stage 1) removes it after.

    Ownership check: tenant_id must match current user; session_id must match
    the attachment row (prevents cross-session reject via stale URL)."""
    from api.db.services.audit_log_service import AuditLogService

    try:
        session = AgentV2SessionService.get_by_id(session_id)
        if not session or session.tenant_id != current_user.id:
            return get_data_error_result(message="session not found")

        row = AgentV2AttachmentService.get_by_id(attachment_id)
        if not row or row.session_id != session_id or row.tenant_id != current_user.id:
            return get_data_error_result(message="attachment not found")

        ok = AgentV2AttachmentService.reject(attachment_id=attachment_id)
        if not ok:
            return get_json_result(
                code=RetCode.ARGUMENT_ERROR,
                message=f"cannot reject: already {row.status}",
            )

        AuditLogService.allow(
            user_id=current_user.id,
            tenant_id=current_user.id,
            action="agent_v2.attachment_reject",
            resource_type="agent_v2_attachment",
            resource_id=attachment_id,
            metadata={"session_id": session_id, "prev_status": row.status},
            request=request,
        )
        return get_json_result(data={"rejected": True})
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

    # Phase 2.6 v0.4 — parse plan decision prefix before storing the message.
    # Supported prefixes (case-insensitive, with or without brackets):
    #   [plan approved]         — user approves the pending plan
    #   [plan rejected]         — user rejects it
    #   [plan request changes]  — user wants an adjusted plan
    # The prefix is stripped from ``user_message`` so downstream code (LLM +
    # message log) sees the clean text. Plan state transitions are written
    # before the runner starts so ``@require_kb_write`` reads fresh state.
    user_message, plan_decision = parse_plan_decision(user_message)
    if plan_decision:
        try:
            AgentV2SessionService.transition_plan_status(session_id, plan_decision)
        except Exception:
            logger.exception("failed to transition plan status to %s", plan_decision)

    # Phase 2.8.3 — Interactive Tool Pause Framework: parse a `[answer: ...]`
    # prefix mirroring the plan-decision flow. plan_decision and
    # question_answer are mutually exclusive in normal usage (the supervisor
    # only emits one pause-tool per turn), but we parse both defensively
    # for robustness against legacy / replayed messages.
    user_message, question_answer = parse_question_answer(user_message)
    if question_answer:
        try:
            AgentV2SessionService.transition_question_status(session_id, "answered")
        except Exception:
            logger.exception("failed to transition question status to answered")

    # Snapshot plan state for the runner so @require_kb_write can gate writes.
    plan_row = None
    try:
        plan_row = AgentV2SessionService.get_pending_plan(session_id)
    except Exception:
        logger.exception("failed to read pending plan status for session %s", session_id)
    plan_status_at_turn_start = (
        (plan_row or {}).get("pending_plan_status") if plan_row else None
    )
    plan_id_at_turn_start = (
        (plan_row or {}).get("pending_plan_id") if plan_row else None
    )

    # 登记 user 消息（保留 id 以便 2.5.2 拉 history 时排除本条）
    # 注意：落库的是**用户原始消息**（已剥 plan / answer 前缀），不包含我们追加
    # 给 LLM 的 meta 指令——那只是 runner 喂 LLM 的上下文，不属于用户说过的话。
    user_msg = AgentV2MessageService.append(
        session_id=session_id, role="user", content=user_message
    )
    user_msg_id = getattr(user_msg, "id", None)

    # Phase 2.6 v0.6-fix — augment the user_message with an explicit directive
    # when a plan decision was parsed. Without this, a bare `[plan approved]`
    # becomes an empty string after prefix-stripping, and the supervisor — which
    # is domain-scoped ("answer housing policy questions") — has no hook telling
    # it to re-spawn the archivist. It falls back to "out-of-domain" and refuses.
    #
    # We build a short instructional prefix the LLM sees as the current
    # user message; the real user text (if any) follows verbatim.
    runner_input = augment_for_plan_decision(
        user_message=user_message,
        plan_decision=plan_decision,
        plan_status=plan_status_at_turn_start,
    )

    # Phase 2.8.3 — same augment dance for ask_user_question resume. Only
    # apply when no plan_decision was already injected (mutual exclusion;
    # stacking two `[xxx system]` directives confuses the supervisor).
    if question_answer and not plan_decision:
        runner_input = augment_for_question_answer(
            user_message=runner_input,
            answer=question_answer,
        )
        # 清空 pending_question_*：本轮已消费，不再让校验器或前端误以为
        # 还在等回复。失败不阻塞——下一轮 set_pending_question 会覆盖。
        try:
            AgentV2SessionService.clear_pending_question(session_id)
        except Exception:
            logger.exception("failed to clear pending_question for session %s", session_id)

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
        include_tool_calls=True,  # v0.5 — keep prior tool activity in history
    )

    # Phase 2.6 v0.6-fix: defensive fallback for legacy sessions that were
    # stored with tool_names=None or {} (pre-fix). Without this, such a
    # session would pass tool_names=None → runner enables ALL 18 tools,
    # handing the supervisor direct write power (doc_tag / doc_reparse /
    # etc.) and bypassing the plan-gate architecture.
    stored_tool_names = session.tool_names
    if isinstance(stored_tool_names, list) and stored_tool_names:
        effective_runtime_tools = list(stored_tool_names)
    else:
        from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS

        effective_runtime_tools = list(SUPERVISOR_TOOLS)

    # Phase 2.7 — snapshot staged/archived attachments for this session so the
    # supervisor prompt can list them and sub_archivist can reach them by id.
    # We intentionally load at turn boundary (not per-tool-call) so the prompt
    # remains stable within a turn.
    from api.agent_v2.attachments import AttachmentInfo

    attachment_rows = AgentV2AttachmentService.list_by_session(
        session_id=session.id, statuses=("staged", "archived"),
    )
    runtime_attachments = tuple(
        AttachmentInfo.from_row(r) for r in attachment_rows
    )

    runner = AgentRunner(
        tenant_id=session.tenant_id,
        kb_ids=list(session.kb_ids or []),
        system_prompt=session.system_prompt or "",
        model=model_cfg,
        tool_names=effective_runtime_tools,
        user_id=session.user_id,
        max_turns=session.max_turns,
        max_budget_usd=session.max_budget_usd,
        session_id=session.id,  # Phase 2.3: 让 spawn_subagent 能引用父 session
        citation_enforce_level=session.citation_enforce_level or "warn",
        citation_numeric_strict=bool(session.citation_numeric_strict),
        pending_plan_status=plan_status_at_turn_start,
        pending_plan_id=plan_id_at_turn_start,
        attachments=runtime_attachments,
    )

    async def stream():
        events: list[dict] = []
        # Phase 2.7 v0.21 — register the runner so the cancel endpoint
        # (POST /v1/agent_v2/session/<id>/cancel) can reach it. Always
        # unregister in finally regardless of how the stream ends —
        # success / error / client disconnect / SDK crash all need to
        # clear the registry entry.
        runner_registry.register(session_id, runner)
        try:
            async for ev in runner.run(
                runner_input,
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
            # v0.21 — drop the runner from the registry so a stale entry
            # doesn't shadow the next turn. Pass ``runner`` so we only
            # drop OUR entry — protects against a late finally racing
            # with a fresh registration if the user fires a new turn
            # immediately after a cancel.
            runner_registry.unregister(session_id, runner)
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

            # Phase 2.6 v0.4 — clear stale plan state at end of turn.
            # If the turn started with status=approved/rejected/request_changes,
            # the agent has had its chance to act on it; leaving the row dirty
            # would make the NEXT turn think there's still a pending decision.
            # A fresh submit_plan this turn re-creates the row as waiting, so
            # we only clear when the DB shows the same non-waiting status we
            # started with.
            if plan_status_at_turn_start in ("approved", "rejected", "request_changes"):
                with contextlib.suppress(Exception):
                    current = AgentV2SessionService.get_pending_plan(session_id)
                    current_status = (
                        (current or {}).get("pending_plan_status") if current else None
                    )
                    if current_status == plan_status_at_turn_start:
                        AgentV2SessionService.clear_pending_plan(session_id)

            # Phase 2.5.2 — 触发 compact（fire-and-forget，不阻塞 SSE 收尾）
            # 用 run_compact_safely 而非 maybe_compact_session：前者保证任何内部
            # 异常都写进 access_audit_log（action=agent_v2.compact），
            # 不会被 asyncio 默认 handler 静默吞掉。
            try:
                from api.agent_v2.compactor import run_compact_safely

                asyncio.create_task(
                    run_compact_safely(
                        session_id=session_id,
                        tenant_id=session.tenant_id,
                        user_id=session.user_id,
                        model=model_cfg.model,
                        base_url=model_cfg.base_url,
                        auth_token=model_cfg.auth_token or "",
                    )
                )
            except Exception:
                logger.exception("failed to schedule compact task for session %s", session_id)

    resp = Response(stream(), mimetype="text/event-stream")
    resp.headers.add_header("Cache-Control", "no-cache")
    resp.headers.add_header("X-Accel-Buffering", "no")
    return resp
