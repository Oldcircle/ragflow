"""Agent v2 持久化服务。

把 Session / Message / ToolCall 的 CRUD 从 Agent 运行时解耦出来。
所有写入均带 @DB.connection_context() 以保证 Peewee 连接池正确归还。
"""

from __future__ import annotations

import json
from typing import Any

from api.db.db_models import DB, AgentV2Message, AgentV2Session, AgentV2ToolCall
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp, datetime_format
from datetime import datetime


def _now_meta() -> dict:
    """统一的时间戳+日期字段值，套到 `DataBaseModel.create/update_time/date`。"""
    ts = current_timestamp()
    date_str = datetime_format(datetime.now())
    return {
        "create_time": ts,
        "create_date": date_str,
        "update_time": ts,
        "update_date": date_str,
    }


# ────────────────────────────── Session ──────────────────────────────


class AgentV2SessionService(CommonService):
    model = AgentV2Session

    @classmethod
    @DB.connection_context()
    def create_session(
        cls,
        *,
        tenant_id: str,
        user_id: str | None,
        name: str,
        kb_ids: list[str],
        system_prompt: str = "",
        tool_names: list[str] | None = None,
        model_config: dict | None = None,
        max_turns: int = 20,
        max_budget_usd: float | None = 1.0,
    ) -> AgentV2Session:
        """创建一个新会话，返回模型实例。"""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not kb_ids:
            raise ValueError("at least one kb_id is required")

        meta = _now_meta()
        session = cls.model.create(
            id=get_uuid(),
            tenant_id=tenant_id,
            user_id=user_id,
            name=name or "Untitled",
            kb_ids=kb_ids,
            tool_names=tool_names,
            system_prompt=system_prompt or "",
            model_config_json=model_config
            or {"model": "claude-sonnet-4-5", "base_url": None, "auth_token_id": None},
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            status="active",
            **meta,
        )
        return session

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, session_id: str) -> AgentV2Session | None:
        rows = list(cls.model.select().where(cls.model.id == session_id))
        return rows[0] if rows else None

    @classmethod
    @DB.connection_context()
    def list_by_tenant(
        cls,
        tenant_id: str,
        user_id: str | None = None,
        status: str = "active",
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict]:
        q = cls.model.select().where(cls.model.tenant_id == tenant_id)
        if user_id is not None:
            q = q.where(cls.model.user_id == user_id)
        if status:
            q = q.where(cls.model.status == status)
        q = q.order_by(cls.model.update_time.desc())
        q = q.paginate(max(1, page), max(1, page_size))
        return list(q.dicts())

    @classmethod
    @DB.connection_context()
    def update_fields(cls, session_id: str, **fields) -> int:
        """只允许更新白名单字段。返回影响行数。"""
        allowed = {
            "name",
            "kb_ids",
            "tool_names",
            "system_prompt",
            "model_config_json",
            "max_turns",
            "max_budget_usd",
            "status",
        }
        to_set: dict[str, Any] = {k: v for k, v in fields.items() if k in allowed}
        if not to_set:
            return 0
        to_set["update_time"] = current_timestamp()
        to_set["update_date"] = datetime_format(datetime.now())
        q = cls.model.update(**to_set).where(cls.model.id == session_id)
        return q.execute()

    @classmethod
    @DB.connection_context()
    def soft_delete(cls, session_id: str) -> int:
        return cls.update_fields(session_id, status="deleted")


# ────────────────────────────── Message ──────────────────────────────


class AgentV2MessageService(CommonService):
    model = AgentV2Message

    @classmethod
    @DB.connection_context()
    def append(
        cls,
        *,
        session_id: str,
        role: str,
        content: str = "",
        thinking: str = "",
        tool_call_ids: list[str] | None = None,
        usage: dict | None = None,
        message_id: str | None = None,
    ) -> AgentV2Message:
        if role not in ("user", "assistant", "system"):
            raise ValueError(f"invalid role: {role!r}")
        meta = _now_meta()
        msg = cls.model.create(
            id=message_id or get_uuid(),
            session_id=session_id,
            role=role,
            content=content or "",
            thinking=thinking or "",
            tool_call_ids=tool_call_ids or [],
            usage=usage or {},
            **meta,
        )
        return msg

    @classmethod
    @DB.connection_context()
    def list_by_session(cls, session_id: str) -> list[dict]:
        q = (
            cls.model.select()
            .where(cls.model.session_id == session_id)
            .order_by(cls.model.create_time.asc())
        )
        return list(q.dicts())


# ────────────────────────────── ToolCall ──────────────────────────────


class AgentV2ToolCallService(CommonService):
    model = AgentV2ToolCall

    @classmethod
    @DB.connection_context()
    def record_start(
        cls,
        *,
        tool_use_id: str,
        session_id: str,
        message_id: str | None,
        tool_name: str,
        args: dict,
    ) -> AgentV2ToolCall:
        meta = _now_meta()
        call = cls.model.create(
            id=tool_use_id,
            session_id=session_id,
            message_id=message_id,
            tool_name=tool_name,
            args=args or {},
            status="pending",
            start_time=current_timestamp(),
            **meta,
        )
        return call

    @classmethod
    @DB.connection_context()
    def record_end(
        cls,
        *,
        tool_use_id: str,
        result: Any,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> int:
        if isinstance(result, (dict, list)):
            result = json.dumps(result, ensure_ascii=False)
        status = "error" if error else "success"
        q = cls.model.update(
            result=result if result is not None else "",
            error=error or "",
            status=status,
            duration_ms=duration_ms or 0,
            update_time=current_timestamp(),
            update_date=datetime_format(datetime.now()),
        ).where(cls.model.id == tool_use_id)
        return q.execute()

    @classmethod
    @DB.connection_context()
    def list_by_session(cls, session_id: str) -> list[dict]:
        q = (
            cls.model.select()
            .where(cls.model.session_id == session_id)
            .order_by(cls.model.start_time.asc())
        )
        return list(q.dicts())
