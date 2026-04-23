"""Agent Trigger 服务（Phase 3.2）。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from peewee import DoesNotExist

from api.db.db_models import DB, AgentTrigger, AgentTriggerRun
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp

logger = logging.getLogger("ragflow.trigger")

VALID_TRIGGER_TYPES = ("cron", "manual", "webhook")
VALID_DELIVERY_KINDS = ("audit_only", "feishu_bot")
VALID_RUN_STATUS = ("running", "success", "error", "timeout", "cancelled")
_MAX_PREVIEW_CHARS = 4096


# ──────────────────────────── cron helpers ────────────────────────────


def compute_next_run_ms(cron_expr: str | None, tz: str | None) -> int | None:
    """用 apscheduler 的 CronTrigger 算下次触发时刻（毫秒）."""
    if not cron_expr:
        return None
    try:
        from apscheduler.triggers.cron import CronTrigger
        import pytz
        tzobj = pytz.timezone(tz or "Asia/Shanghai")
        trig = CronTrigger.from_crontab(cron_expr, timezone=tzobj)
        now = datetime.now(tzobj)
        next_fire = trig.get_next_fire_time(None, now)
        if next_fire is None:
            return None
        return int(next_fire.timestamp() * 1000)
    except Exception as e:
        logger.warning("compute_next_run_ms failed for %r: %s", cron_expr, e)
        return None


def validate_cron(cron_expr: str) -> None:
    """crontab 语法校验；不合法抛 ValueError."""
    from apscheduler.triggers.cron import CronTrigger
    try:
        CronTrigger.from_crontab(cron_expr)
    except Exception as e:
        raise ValueError(f"invalid cron expression: {e}") from e


# ──────────────────────────── trigger CRUD ────────────────────────────


class AgentTriggerService(CommonService):
    model = AgentTrigger

    @classmethod
    @DB.connection_context()
    def list_by_tenant(cls, tenant_id: str) -> list[AgentTrigger]:
        return list(AgentTrigger.select().where(
            AgentTrigger.tenant_id == tenant_id
        ).order_by(AgentTrigger.create_time.desc()))

    @classmethod
    @DB.connection_context()
    def get_by_id_for_tenant(cls, trigger_id: str, tenant_id: str) -> AgentTrigger | None:
        try:
            return AgentTrigger.select().where(
                (AgentTrigger.id == trigger_id)
                & (AgentTrigger.tenant_id == tenant_id)
            ).get()
        except DoesNotExist:
            return None

    @classmethod
    @DB.connection_context()
    def create(
        cls,
        *,
        tenant_id: str,
        created_by: str,
        name: str,
        description: str = "",
        trigger_type: str = "cron",
        cron_expr: str | None = None,
        timezone: str = "Asia/Shanghai",
        agent_session_id: str,
        prompt: str,
        max_turns: int = 20,
        max_budget_usd: float | None = 1.0,
        delivery_kind: str = "audit_only",
        delivery_config: dict | None = None,
        enabled: bool = True,
    ) -> AgentTrigger:
        if trigger_type not in VALID_TRIGGER_TYPES:
            raise ValueError(f"trigger_type must be one of {VALID_TRIGGER_TYPES}")
        if delivery_kind not in VALID_DELIVERY_KINDS:
            raise ValueError(f"delivery_kind must be one of {VALID_DELIVERY_KINDS}")
        if trigger_type == "cron":
            if not cron_expr:
                raise ValueError("cron_expr is required when trigger_type=cron")
            validate_cron(cron_expr)
        if not agent_session_id:
            raise ValueError("agent_session_id is required")
        if not prompt:
            raise ValueError("prompt cannot be empty")

        next_run_at = (
            compute_next_run_ms(cron_expr, timezone)
            if trigger_type == "cron" and enabled else None
        )

        return AgentTrigger.create(
            id=get_uuid(),
            tenant_id=tenant_id,
            created_by=created_by,
            name=name,
            description=description or "",
            trigger_type=trigger_type,
            cron_expr=cron_expr,
            timezone=timezone or "Asia/Shanghai",
            agent_session_id=agent_session_id,
            prompt=prompt,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            delivery_kind=delivery_kind,
            delivery_config=delivery_config or {},
            enabled=1 if enabled else 0,
            next_run_at=next_run_at,
        )

    @classmethod
    @DB.connection_context()
    def update(cls, trigger_id: str, tenant_id: str, fields: dict[str, Any]) -> int:
        allowed = {
            "name", "description",
            "cron_expr", "timezone",
            "prompt", "max_turns", "max_budget_usd",
            "delivery_kind", "delivery_config",
            "enabled",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        if "delivery_kind" in clean and clean["delivery_kind"] not in VALID_DELIVERY_KINDS:
            raise ValueError("invalid delivery_kind")
        if "cron_expr" in clean and clean["cron_expr"]:
            validate_cron(clean["cron_expr"])
        if "enabled" in clean:
            clean["enabled"] = 1 if clean["enabled"] else 0
        if not clean:
            return 0

        current = cls.get_by_id_for_tenant(trigger_id, tenant_id)
        if current is None:
            return 0

        # 如果改了 cron / enabled / tz，就要重算 next_run_at
        new_cron = clean.get("cron_expr", current.cron_expr)
        new_tz = clean.get("timezone", current.timezone)
        new_enabled = clean.get("enabled", current.enabled)
        if new_enabled and current.trigger_type == "cron":
            clean["next_run_at"] = compute_next_run_ms(new_cron, new_tz)
        elif not new_enabled:
            clean["next_run_at"] = None

        return AgentTrigger.update(**clean).where(
            (AgentTrigger.id == trigger_id)
            & (AgentTrigger.tenant_id == tenant_id)
        ).execute()

    @classmethod
    @DB.connection_context()
    def delete(cls, trigger_id: str, tenant_id: str) -> int:
        AgentTriggerRun.delete().where(
            AgentTriggerRun.trigger_id == trigger_id
        ).execute()
        return AgentTrigger.delete().where(
            (AgentTrigger.id == trigger_id)
            & (AgentTrigger.tenant_id == tenant_id)
        ).execute()

    @classmethod
    @DB.connection_context()
    def due(cls, now_ms: int, limit: int = 50) -> list[AgentTrigger]:
        """返回已到期的启用触发器（worker 一轮 tick 消费）."""
        return list(AgentTrigger.select().where(
            (AgentTrigger.enabled == 1)
            & (AgentTrigger.trigger_type == "cron")
            & (AgentTrigger.next_run_at.is_null(False))
            & (AgentTrigger.next_run_at <= now_ms)
        ).order_by(AgentTrigger.next_run_at.asc()).limit(limit))

    @classmethod
    @DB.connection_context()
    def mark_ran(
        cls,
        trigger_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        """更新 trigger 的 last_run_* 字段 + 重算 next_run_at（保持 cron 循环）."""
        now = current_timestamp()
        try:
            row = AgentTrigger.select().where(AgentTrigger.id == trigger_id).get()
        except DoesNotExist:
            return
        next_run = None
        if row.enabled and row.trigger_type == "cron":
            next_run = compute_next_run_ms(row.cron_expr, row.timezone)
        AgentTrigger.update(
            last_run_at=now,
            last_run_status=status,
            last_run_error=(error or "")[:255] if error else None,
            next_run_at=next_run,
        ).where(AgentTrigger.id == trigger_id).execute()


# ──────────────────────────── run history ────────────────────────────


class AgentTriggerRunService(CommonService):
    model = AgentTriggerRun

    @classmethod
    @DB.connection_context()
    def start(
        cls,
        *,
        trigger_id: str,
        tenant_id: str,
        kicked_by: str = "scheduler",
    ) -> AgentTriggerRun:
        return AgentTriggerRun.create(
            id=get_uuid(),
            trigger_id=trigger_id,
            tenant_id=tenant_id,
            kicked_by=kicked_by,
            status="running",
            started_at=current_timestamp(),
        )

    @classmethod
    @DB.connection_context()
    def finish(
        cls,
        run_id: str,
        *,
        status: str,
        result_preview: str | None = None,
        error: str | None = None,
        token_usage: dict | None = None,
        cost_usd: float | None = None,
        delivery_status: str | None = None,
        delivery_error: str | None = None,
    ) -> None:
        now = current_timestamp()
        try:
            row = AgentTriggerRun.select().where(
                AgentTriggerRun.id == run_id
            ).get()
        except DoesNotExist:
            return
        duration = max(0, now - (row.started_at or now))
        preview = (result_preview or "")[:_MAX_PREVIEW_CHARS]
        AgentTriggerRun.update(
            status=status,
            result_preview=preview,
            error=error,
            token_usage_json=token_usage,
            cost_usd=cost_usd,
            delivery_status=delivery_status,
            delivery_error=delivery_error,
            completed_at=now,
            duration_ms=duration,
        ).where(AgentTriggerRun.id == run_id).execute()

    @classmethod
    @DB.connection_context()
    def list_by_trigger(
        cls, trigger_id: str, limit: int = 50
    ) -> list[AgentTriggerRun]:
        return list(AgentTriggerRun.select().where(
            AgentTriggerRun.trigger_id == trigger_id
        ).order_by(AgentTriggerRun.started_at.desc()).limit(limit))
