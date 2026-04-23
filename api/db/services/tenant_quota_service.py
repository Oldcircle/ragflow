"""租户配额 + 日用量服务（Phase 3.1b）。

设计原则：
  - 每次 increment 都是一个 UPSERT 级原子操作（先查行，没行就建，存在就 UPDATE + 算数）
  - 查询时遇到无 quota 记录的 tenant 返回硬编码默认值（不触发写入，避免激活未使用 tenant）
  - `hard_enforce = 0`（默认）：超额只产审计 + 返 metrics，仍放行（防止首次部署就把所有人锁死）
  - `hard_enforce = 1`：enforce_* 函数会抛 QuotaExceeded 让调用层返 429/403
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from peewee import DoesNotExist, IntegrityError

from api.db.db_models import DB, TenantQuota, TenantUsageDaily
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid

# ────────────────────────────── 默认配额 ──────────────────────────────

DEFAULT_QUOTA = {
    "kb_max": 50,
    "doc_max": 10_000,
    "token_month_max": 50_000_000,
    "api_rps_max": 20,
    "bot_message_day_max": 5_000,
    "subagent_day_max": 2_000,
    "hard_enforce": 0,
}

_QUOTA_FIELDS = tuple(DEFAULT_QUOTA.keys())


@dataclass
class QuotaLimits:
    kb_max: int
    doc_max: int
    token_month_max: int
    api_rps_max: int
    bot_message_day_max: int
    subagent_day_max: int
    hard_enforce: bool

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["hard_enforce"] = bool(self.hard_enforce)
        return d


class QuotaExceeded(Exception):
    """配额超限；调用层翻成 HTTP 429."""

    def __init__(self, metric: str, limit: int | float, used: int | float,
                 message: str | None = None):
        super().__init__(message or f"{metric} quota exceeded: {used}/{limit}")
        self.metric = metric
        self.limit = limit
        self.used = used


# ────────────────────────────── Quota CRUD ──────────────────────────────


class TenantQuotaService(CommonService):
    model = TenantQuota

    @classmethod
    @DB.connection_context()
    def get(cls, tenant_id: str) -> QuotaLimits:
        """总是返回有值；不存在时走默认值（但不落库）。"""
        try:
            row = TenantQuota.select().where(
                TenantQuota.tenant_id == tenant_id
            ).get()
            return QuotaLimits(
                kb_max=row.kb_max,
                doc_max=row.doc_max,
                token_month_max=row.token_month_max,
                api_rps_max=row.api_rps_max,
                bot_message_day_max=row.bot_message_day_max,
                subagent_day_max=row.subagent_day_max,
                hard_enforce=bool(row.hard_enforce),
            )
        except DoesNotExist:
            return QuotaLimits(
                **{k: v for k, v in DEFAULT_QUOTA.items() if k != "hard_enforce"},
                hard_enforce=bool(DEFAULT_QUOTA["hard_enforce"]),
            )

    @classmethod
    @DB.connection_context()
    def set_limits(cls, tenant_id: str, fields: dict[str, Any]) -> None:
        """Super-admin 改配额。v1 没暴露前端 UI，由直接改表或内部脚本触发."""
        clean = {k: v for k, v in fields.items() if k in _QUOTA_FIELDS}
        if not clean:
            return
        updated = TenantQuota.update(**clean).where(
            TenantQuota.tenant_id == tenant_id
        ).execute()
        if updated == 0:
            # 建行
            TenantQuota.create(
                id=get_uuid(),
                tenant_id=tenant_id,
                **{**DEFAULT_QUOTA, **clean},
            )


# ────────────────────────────── Usage CRUD ──────────────────────────────


def _today_ymd() -> int:
    d = datetime.datetime.now()
    return d.year * 10000 + d.month * 100 + d.day


def _month_start_ymd() -> int:
    d = datetime.datetime.now()
    return d.year * 10000 + d.month * 100 + 1


class TenantUsageService(CommonService):
    model = TenantUsageDaily

    @classmethod
    @DB.connection_context()
    def _ensure_row(cls, tenant_id: str, ymd: int) -> TenantUsageDaily:
        """找/建当天行；并发安全（靠 unique 索引）."""
        try:
            return TenantUsageDaily.select().where(
                (TenantUsageDaily.tenant_id == tenant_id)
                & (TenantUsageDaily.date_ymd == ymd)
            ).get()
        except DoesNotExist:
            try:
                return TenantUsageDaily.create(
                    id=get_uuid(),
                    tenant_id=tenant_id,
                    date_ymd=ymd,
                )
            except IntegrityError:
                return TenantUsageDaily.select().where(
                    (TenantUsageDaily.tenant_id == tenant_id)
                    & (TenantUsageDaily.date_ymd == ymd)
                ).get()

    @classmethod
    @DB.connection_context()
    def increment(
        cls,
        tenant_id: str,
        *,
        token_in: int = 0,
        token_out: int = 0,
        cost_usd: float = 0.0,
        api_requests: int = 0,
        bot_messages: int = 0,
        subagent_spawns: int = 0,
    ) -> None:
        if not tenant_id:
            return
        ymd = _today_ymd()
        cls._ensure_row(tenant_id, ymd)
        updates: dict[str, Any] = {}
        if token_in:
            updates["token_in"] = TenantUsageDaily.token_in + int(token_in)
        if token_out:
            updates["token_out"] = TenantUsageDaily.token_out + int(token_out)
        if cost_usd:
            updates["cost_usd"] = TenantUsageDaily.cost_usd + float(cost_usd)
        if api_requests:
            updates["api_requests"] = (
                TenantUsageDaily.api_requests + int(api_requests)
            )
        if bot_messages:
            updates["bot_messages"] = (
                TenantUsageDaily.bot_messages + int(bot_messages)
            )
        if subagent_spawns:
            updates["subagent_spawns"] = (
                TenantUsageDaily.subagent_spawns + int(subagent_spawns)
            )
        if updates:
            TenantUsageDaily.update(**updates).where(
                (TenantUsageDaily.tenant_id == tenant_id)
                & (TenantUsageDaily.date_ymd == ymd)
            ).execute()

    @classmethod
    @DB.connection_context()
    def get_today(cls, tenant_id: str) -> dict:
        ymd = _today_ymd()
        try:
            row = TenantUsageDaily.select().where(
                (TenantUsageDaily.tenant_id == tenant_id)
                & (TenantUsageDaily.date_ymd == ymd)
            ).get()
        except DoesNotExist:
            return _empty_usage(ymd)
        return _row_to_dict(row)

    @classmethod
    @DB.connection_context()
    def get_month(cls, tenant_id: str) -> dict:
        """当月累计（当天服务器日历月）."""
        start = _month_start_ymd()
        end = _today_ymd()
        totals = {
            "token_in": 0, "token_out": 0, "cost_usd": 0.0,
            "api_requests": 0, "bot_messages": 0, "subagent_spawns": 0,
        }
        for row in TenantUsageDaily.select().where(
            (TenantUsageDaily.tenant_id == tenant_id)
            & (TenantUsageDaily.date_ymd >= start)
            & (TenantUsageDaily.date_ymd <= end)
        ):
            totals["token_in"] += row.token_in
            totals["token_out"] += row.token_out
            totals["cost_usd"] += row.cost_usd
            totals["api_requests"] += row.api_requests
            totals["bot_messages"] += row.bot_messages
            totals["subagent_spawns"] += row.subagent_spawns
        return totals

    @classmethod
    @DB.connection_context()
    def get_range(cls, tenant_id: str, days: int = 30) -> list[dict]:
        """最近 `days` 天的日用量（按日期升序）."""
        today = datetime.date.today()
        start_date = today - datetime.timedelta(days=max(1, days) - 1)
        start_ymd = start_date.year * 10000 + start_date.month * 100 + start_date.day
        rows = list(TenantUsageDaily.select().where(
            (TenantUsageDaily.tenant_id == tenant_id)
            & (TenantUsageDaily.date_ymd >= start_ymd)
        ).order_by(TenantUsageDaily.date_ymd.asc()))
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    return {
        "date_ymd": row.date_ymd,
        "token_in": row.token_in,
        "token_out": row.token_out,
        "cost_usd": row.cost_usd,
        "api_requests": row.api_requests,
        "bot_messages": row.bot_messages,
        "subagent_spawns": row.subagent_spawns,
    }


def _empty_usage(ymd: int) -> dict:
    return {
        "date_ymd": ymd, "token_in": 0, "token_out": 0, "cost_usd": 0.0,
        "api_requests": 0, "bot_messages": 0, "subagent_spawns": 0,
    }


# ────────────────────────────── Enforcement ──────────────────────────────


def check_token_monthly(tenant_id: str) -> tuple[int, int]:
    """返回 (当月 token_in+out, 月上限)。

    若 hard_enforce=1 且超限则抛 QuotaExceeded；否则仅返回度量供调用方记审计。
    """
    q = TenantQuotaService.get(tenant_id)
    month = TenantUsageService.get_month(tenant_id)
    used = (month["token_in"] or 0) + (month["token_out"] or 0)
    limit = q.token_month_max
    if limit > 0 and used >= limit and q.hard_enforce:
        raise QuotaExceeded("token_month", limit, used)
    return used, limit


def check_bot_message_daily(tenant_id: str) -> tuple[int, int]:
    q = TenantQuotaService.get(tenant_id)
    today = TenantUsageService.get_today(tenant_id)
    used = today["bot_messages"] or 0
    limit = q.bot_message_day_max
    if limit > 0 and used >= limit and q.hard_enforce:
        raise QuotaExceeded("bot_message_day", limit, used)
    return used, limit


def check_subagent_daily(tenant_id: str) -> tuple[int, int]:
    q = TenantQuotaService.get(tenant_id)
    today = TenantUsageService.get_today(tenant_id)
    used = today["subagent_spawns"] or 0
    limit = q.subagent_day_max
    if limit > 0 and used >= limit and q.hard_enforce:
        raise QuotaExceeded("subagent_day", limit, used)
    return used, limit
