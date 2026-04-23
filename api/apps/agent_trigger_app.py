"""Agent Trigger CRUD + 手动运行 + 运行历史（Phase 3.2）。

URL 前缀：``/v1/agent_trigger``（由 register_page 自动注册）
"""

from __future__ import annotations

import logging

from quart import request

from api.apps import current_user, login_required
from api.db.services.agent_trigger_service import (
    AgentTriggerRunService,
    AgentTriggerService,
    compute_next_run_ms,
    validate_cron,
)
from api.db.services.agent_v2_service import AgentV2SessionService
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    get_request_json,
    server_error_response,
    validate_request,
)
from common.constants import RetCode

logger = logging.getLogger("ragflow.trigger.http")


def _to_dict(t) -> dict:
    return {
        "id": t.id,
        "tenant_id": t.tenant_id,
        "name": t.name,
        "description": t.description,
        "trigger_type": t.trigger_type,
        "cron_expr": t.cron_expr,
        "timezone": t.timezone,
        "agent_session_id": t.agent_session_id,
        "prompt": t.prompt,
        "max_turns": t.max_turns,
        "max_budget_usd": t.max_budget_usd,
        "delivery_kind": t.delivery_kind,
        "delivery_config": t.delivery_config,
        "enabled": bool(t.enabled),
        "next_run_at": t.next_run_at,
        "last_run_at": t.last_run_at,
        "last_run_status": t.last_run_status,
        "last_run_error": t.last_run_error,
        "create_time": t.create_time,
        "update_time": t.update_time,
    }


def _run_to_dict(r) -> dict:
    return {
        "id": r.id,
        "trigger_id": r.trigger_id,
        "tenant_id": r.tenant_id,
        "kicked_by": r.kicked_by,
        "status": r.status,
        "started_at": r.started_at,
        "completed_at": r.completed_at,
        "duration_ms": r.duration_ms,
        "result_preview": r.result_preview,
        "error": r.error,
        "token_usage_json": r.token_usage_json,
        "cost_usd": r.cost_usd,
        "delivery_status": r.delivery_status,
        "delivery_error": r.delivery_error,
    }


@manager.route("/list", methods=["GET"])  # noqa: F821
@login_required
async def list_triggers():
    try:
        rows = AgentTriggerService.list_by_tenant(current_user.id)
        return get_json_result(data={"triggers": [_to_dict(r) for r in rows]})
    except Exception as e:
        return server_error_response(e)


@manager.route("/<trigger_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_trigger(trigger_id: str):
    try:
        t = AgentTriggerService.get_by_id_for_tenant(trigger_id, current_user.id)
        if t is None:
            return get_json_result(code=RetCode.NOT_FOUND, message="not found")
        return get_json_result(data=_to_dict(t))
    except Exception as e:
        return server_error_response(e)


@manager.route("", methods=["POST"])  # noqa: F821
@login_required
@validate_request("name", "agent_session_id", "prompt")
async def create_trigger():
    try:
        req = await get_request_json()

        # 校验目标 session 属于当前租户
        sess = AgentV2SessionService.get_by_id(req["agent_session_id"])
        if not sess or sess.tenant_id != current_user.id:
            return get_data_error_result(message="agent_session_id not accessible")

        trigger_type = req.get("trigger_type", "cron")
        cron_expr = req.get("cron_expr")
        if trigger_type == "cron":
            if not cron_expr:
                return get_data_error_result(
                    message="cron_expr is required for cron triggers",
                )
            try:
                validate_cron(cron_expr)
            except ValueError as e:
                return get_data_error_result(message=str(e))

        t = AgentTriggerService.create(
            tenant_id=current_user.id,
            created_by=current_user.id,
            name=req["name"],
            description=req.get("description") or "",
            trigger_type=trigger_type,
            cron_expr=cron_expr,
            timezone=req.get("timezone", "Asia/Shanghai"),
            agent_session_id=req["agent_session_id"],
            prompt=req["prompt"],
            max_turns=int(req.get("max_turns") or 20),
            max_budget_usd=req.get("max_budget_usd") or 1.0,
            delivery_kind=req.get("delivery_kind", "audit_only"),
            delivery_config=req.get("delivery_config") or {},
            enabled=req.get("enabled", True),
        )
        return get_json_result(data=_to_dict(t))
    except ValueError as e:
        return get_data_error_result(message=str(e))
    except Exception as e:
        return server_error_response(e)


@manager.route("/<trigger_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_trigger(trigger_id: str):
    try:
        req = await get_request_json()
        existing = AgentTriggerService.get_by_id_for_tenant(trigger_id, current_user.id)
        if existing is None:
            return get_json_result(code=RetCode.NOT_FOUND, message="not found")
        updated = AgentTriggerService.update(trigger_id, current_user.id, req)
        if updated == 0:
            return get_data_error_result(message="nothing to update")
        t = AgentTriggerService.get_by_id_for_tenant(trigger_id, current_user.id)
        return get_json_result(data=_to_dict(t) if t else {})
    except ValueError as e:
        return get_data_error_result(message=str(e))
    except Exception as e:
        return server_error_response(e)


@manager.route("/<trigger_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_trigger(trigger_id: str):
    try:
        deleted = AgentTriggerService.delete(trigger_id, current_user.id)
        return get_json_result(data={"deleted": deleted})
    except Exception as e:
        return server_error_response(e)


@manager.route("/<trigger_id>/run", methods=["POST"])  # noqa: F821
@login_required
async def run_trigger_now(trigger_id: str):
    """手动立即运行一次（同步等结果）。"""
    try:
        t = AgentTriggerService.get_by_id_for_tenant(trigger_id, current_user.id)
        if t is None:
            return get_json_result(code=RetCode.NOT_FOUND, message="not found")

        # 直接 await；路由已经是 async
        from api.agent_v2.trigger_worker import _run_one
        result = await _run_one(t, kicked_by="manual")
        return get_json_result(data=result)
    except Exception as e:
        return server_error_response(e)


@manager.route("/<trigger_id>/run", methods=["GET"])  # noqa: F821
@login_required
async def list_trigger_runs(trigger_id: str):
    try:
        t = AgentTriggerService.get_by_id_for_tenant(trigger_id, current_user.id)
        if t is None:
            return get_json_result(code=RetCode.NOT_FOUND, message="not found")
        rows = AgentTriggerRunService.list_by_trigger(trigger_id, limit=50)
        return get_json_result(data={"runs": [_run_to_dict(r) for r in rows]})
    except Exception as e:
        return server_error_response(e)


@manager.route("/cron_preview", methods=["POST"])  # noqa: F821
@login_required
@validate_request("cron_expr")
async def cron_preview():
    """帮前端预览给定 cron 表达式的下 5 次触发时刻."""
    try:
        req = await get_request_json()
        expr = req["cron_expr"]
        tz = req.get("timezone", "Asia/Shanghai")

        try:
            validate_cron(expr)
        except ValueError as e:
            return get_data_error_result(message=str(e))

        from apscheduler.triggers.cron import CronTrigger
        from datetime import datetime
        import pytz
        tzobj = pytz.timezone(tz)
        trig = CronTrigger.from_crontab(expr, timezone=tzobj)
        now = datetime.now(tzobj)
        fires: list[int] = []
        for _ in range(5):
            nxt = trig.get_next_fire_time(None, now)
            if nxt is None:
                break
            fires.append(int(nxt.timestamp() * 1000))
            now = nxt
        return get_json_result(data={"next_runs": fires})
    except Exception as e:
        return server_error_response(e)
