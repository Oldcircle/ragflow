"""子 Agent 执行轨迹服务（Phase 2.3）。"""

from __future__ import annotations

from typing import Any

from peewee import DoesNotExist

from api.db.db_models import DB, AgentV2SubagentTrace
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp

_MAX_PREVIEW_CHARS = 4096


class SubagentTraceService(CommonService):
    model = AgentV2SubagentTrace

    @classmethod
    @DB.connection_context()
    def start(
        cls,
        *,
        parent_session_id: str,
        parent_tool_call_id: str,
        description: str,
        prompt: str,
        allowed_tools: list[str] | None,
        max_turns: int,
        max_budget_usd: float | None,
    ) -> AgentV2SubagentTrace:
        return AgentV2SubagentTrace.create(
            id=get_uuid(),
            parent_session_id=parent_session_id,
            parent_tool_call_id=parent_tool_call_id,
            description=description or "",
            prompt=prompt or "",
            allowed_tools=list(allowed_tools or []),
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            status="running",
            start_time=current_timestamp(),
        )

    @classmethod
    @DB.connection_context()
    def finish(
        cls,
        trace_id: str,
        *,
        status: str,
        result_preview: str | None = None,
        error: str | None = None,
        token_usage: dict[str, Any] | None = None,
        cost_usd: float | None = None,
    ) -> int:
        preview = (result_preview or "")[:_MAX_PREVIEW_CHARS]
        now = current_timestamp()
        trace = cls.get_by_id(trace_id)
        duration = None
        if trace and trace.start_time:
            duration = max(0, now - trace.start_time)
        return AgentV2SubagentTrace.update(
            status=status,
            result_preview=preview,
            error=error,
            token_usage_json=token_usage,
            cost_usd=cost_usd,
            duration_ms=duration,
            end_time=now,
        ).where(AgentV2SubagentTrace.id == trace_id).execute()

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, trace_id: str) -> AgentV2SubagentTrace | None:
        try:
            return AgentV2SubagentTrace.select().where(
                AgentV2SubagentTrace.id == trace_id
            ).get()
        except DoesNotExist:
            return None

    @classmethod
    @DB.connection_context()
    def list_by_session(
        cls, session_id: str, limit: int = 200
    ) -> list[AgentV2SubagentTrace]:
        return list(
            AgentV2SubagentTrace.select().where(
                AgentV2SubagentTrace.parent_session_id == session_id
            ).order_by(AgentV2SubagentTrace.start_time.asc()).limit(limit)
        )
