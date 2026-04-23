"""访问审计日志服务（Phase 2.1）。

写入 access_audit_log 表。所有 deny 必须记录；allow 调用方按需记录。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from api.db.db_models import DB, AccessAuditLog
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid

logger = logging.getLogger("ragflow.audit")


class AuditLogService(CommonService):
    model = AccessAuditLog

    @classmethod
    @DB.connection_context()
    def log(
        cls,
        *,
        user_id: str | None,
        tenant_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        result: Literal["allow", "deny"],
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        request: Any = None,
    ) -> None:
        """写一条审计记录。

        Args:
            request: flask Request 实例（可选）；提供则自动抓 IP / User-Agent。
                     可以是任意有 .headers / .remote_addr / .user_agent 属性的对象。
        """
        ip = None
        user_agent = None
        if request is not None:
            try:
                ip = (
                    (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                    or getattr(request, "remote_addr", None)
                )
                ua = getattr(request, "user_agent", None)
                user_agent = str(ua) if ua else None
            except Exception:
                pass  # audit shouldn't crash callers

        try:
            AccessAuditLog.create(
                id=get_uuid(),
                user_id=user_id,
                tenant_id=tenant_id or "",
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                result=result,
                reason=reason,
                metadata=metadata or {},
                ip=ip,
                user_agent=user_agent,
            )
        except Exception:
            # 审计失败绝不阻塞业务流程；只打日志
            logger.exception(
                "audit log failed: action=%s user=%s tenant=%s resource=%s/%s result=%s",
                action, user_id, tenant_id, resource_type, resource_id, result,
            )

    # 便捷别名
    @classmethod
    def deny(cls, **kwargs: Any) -> None:
        cls.log(result="deny", **kwargs)

    @classmethod
    def allow(cls, **kwargs: Any) -> None:
        cls.log(result="allow", **kwargs)

    @classmethod
    @DB.connection_context()
    def query_logs(
        cls,
        *,
        tenant_id: str,
        user_id: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        result: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AccessAuditLog]:
        """分页查询；按 create_time 倒序。"""
        q = AccessAuditLog.select().where(AccessAuditLog.tenant_id == tenant_id)
        if user_id:
            q = q.where(AccessAuditLog.user_id == user_id)
        if action:
            q = q.where(AccessAuditLog.action == action)
        if resource_type:
            q = q.where(AccessAuditLog.resource_type == resource_type)
        if resource_id:
            q = q.where(AccessAuditLog.resource_id == resource_id)
        if result:
            q = q.where(AccessAuditLog.result == result)
        if start_ms:
            q = q.where(AccessAuditLog.create_time >= start_ms)
        if end_ms:
            q = q.where(AccessAuditLog.create_time <= end_ms)
        q = q.order_by(AccessAuditLog.create_time.desc()).limit(limit).offset(offset)
        return list(q)

    @classmethod
    @DB.connection_context()
    def count_logs(
        cls,
        *,
        tenant_id: str,
        user_id: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        result: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> int:
        q = AccessAuditLog.select().where(AccessAuditLog.tenant_id == tenant_id)
        if user_id:
            q = q.where(AccessAuditLog.user_id == user_id)
        if action:
            q = q.where(AccessAuditLog.action == action)
        if resource_type:
            q = q.where(AccessAuditLog.resource_type == resource_type)
        if resource_id:
            q = q.where(AccessAuditLog.resource_id == resource_id)
        if result:
            q = q.where(AccessAuditLog.result == result)
        if start_ms:
            q = q.where(AccessAuditLog.create_time >= start_ms)
        if end_ms:
            q = q.where(AccessAuditLog.create_time <= end_ms)
        return q.count()
