"""Agent v2 持久化服务。

把 Session / Message / ToolCall 的 CRUD 从 Agent 运行时解耦出来。
所有写入均带 @DB.connection_context() 以保证 Peewee 连接池正确归还。
"""

from __future__ import annotations

import json
from typing import Any

from api.db.db_models import (
    DB,
    AgentV2Attachment,
    AgentV2Message,
    AgentV2Session,
    AgentV2ToolCall,
)
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
        citation_enforce_level: str = "warn",
        citation_numeric_strict: bool = True,
        history_turn_limit: int = 10,
    ) -> AgentV2Session:
        """创建一个新会话，返回模型实例。"""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not kb_ids:
            raise ValueError("at least one kb_id is required")

        if citation_enforce_level not in ("off", "warn", "strict"):
            citation_enforce_level = "warn"

        # history_turn_limit 限定合理范围，防止 prompt 爆
        history_turn_limit = max(2, min(40, int(history_turn_limit or 10)))

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
            citation_enforce_level=citation_enforce_level,
            citation_numeric_strict=1 if citation_numeric_strict else 0,
            history_turn_limit=history_turn_limit,
            summary_text="",
            summary_until_seq=0,
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
    def save_summary(
        cls,
        session_id: str,
        *,
        summary_text: str,
        summary_until_seq: int,
    ) -> int:
        """把 compact 后的摘要写回 session。"""
        q = cls.model.update(
            summary_text=summary_text or "",
            summary_until_seq=int(summary_until_seq or 0),
            update_time=current_timestamp(),
            update_date=datetime_format(datetime.now()),
        ).where(cls.model.id == session_id)
        return q.execute()

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
            "history_turn_limit",
            "citation_enforce_level",
            "citation_numeric_strict",
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

    # ────────── Phase 2.6 v0.4 — real plan gating ──────────
    #
    # submit_plan 和用户回复之间隔着一整个 HTTP turn。为了让下一轮的
    # ``@require_kb_write`` 能知道"用户到底点了 Approve 还是 Reject"，这三
    # 个方法管理 ``AgentV2Session`` 上的 ``pending_plan_*`` 列。
    #
    # 合法状态机：
    #   NULL ─set_pending_plan→ "waiting" ─transition_status→ approved / rejected / request_changes
    #   {approved, rejected, request_changes} ─clear_pending_plan→ NULL

    _VALID_PLAN_STATUSES = ("waiting", "approved", "rejected", "request_changes")

    @classmethod
    @DB.connection_context()
    def set_pending_plan(
        cls,
        session_id: str,
        pending_id: str,
        plan_body: dict | None = None,
    ) -> int:
        """submit_plan 调用后标记 session 进入 'waiting' 状态。

        Phase 2.6 v0.6 — ``plan_body`` 存整个 plan payload，让下一轮 approved
        的 Agent 能通过 ``get_pending_plan`` 工具读回 steps / affected_resources
        等，不用从 tool_call 历史里间接重建。
        """
        updates: dict = {
            "pending_plan_id": pending_id,
            "pending_plan_status": "waiting",
            "pending_plan_submitted_at": current_timestamp(),
            "update_time": current_timestamp(),
            "update_date": datetime_format(datetime.now()),
        }
        if plan_body is not None:
            updates["pending_plan_body"] = plan_body
        q = cls.model.update(**updates).where(cls.model.id == session_id)
        return q.execute()

    @classmethod
    @DB.connection_context()
    def transition_plan_status(cls, session_id: str, status: str) -> int:
        """把 waiting 推进到 approved / rejected / request_changes。"""
        if status not in cls._VALID_PLAN_STATUSES:
            raise ValueError(
                f"invalid plan status {status!r}; must be one of "
                f"{cls._VALID_PLAN_STATUSES}"
            )
        q = cls.model.update(
            pending_plan_status=status,
            update_time=current_timestamp(),
            update_date=datetime_format(datetime.now()),
        ).where(cls.model.id == session_id)
        return q.execute()

    @classmethod
    @DB.connection_context()
    def clear_pending_plan(cls, session_id: str) -> int:
        """写完后清空，保证每个 plan 只解一次锁。Phase 2.6 v0.6 起同时清 body。"""
        q = cls.model.update(
            pending_plan_id=None,
            pending_plan_status=None,
            pending_plan_submitted_at=None,
            pending_plan_body=None,
            update_time=current_timestamp(),
            update_date=datetime_format(datetime.now()),
        ).where(cls.model.id == session_id)
        return q.execute()

    @classmethod
    @DB.connection_context()
    def get_pending_plan(cls, session_id: str, include_body: bool = False) -> dict | None:
        """读回当前 session 的 plan 状态；查不到返 None。

        Phase 2.6 v0.6 — ``include_body=True`` 会把 plan 完整 payload 一起
        拉回；`@require_kb_write` 的 gate 只需要 status 所以默认不拉 body，
        新工具 `get_pending_plan` 显式要 body。
        """
        cols = [
            cls.model.pending_plan_id,
            cls.model.pending_plan_status,
            cls.model.pending_plan_submitted_at,
        ]
        if include_body:
            cols.append(cls.model.pending_plan_body)
        rows = list(
            cls.model.select(*cols).where(cls.model.id == session_id).dicts()
        )
        if not rows:
            return None
        row = rows[0]
        if not row.get("pending_plan_status"):
            return None
        return row


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

    @classmethod
    @DB.connection_context()
    def list_for_runner(
        cls,
        *,
        session_id: str,
        limit: int = 10,
        exclude_message_id: str | None = None,
        since_create_time: int | None = None,
        include_tool_calls: bool = False,
    ) -> list[dict]:
        """返回喂给 AgentRunner 的历史消息（按时间升序）。

        - 只取 role 为 user / assistant 的消息
        - `since_create_time` 可限定只取某个时间点之后（compact 后传
          ``summary_until_seq`` 对应的时间戳，跳过已总结的旧消息）
        - `exclude_message_id` 用来跳过本轮刚落库的空 assistant 占位
        - `limit` 是"消息数"（不是轮次），UI 约定用户输入 1 条 + 助手 1 条算 1 轮
          所以这里 limit=20 相当于约 10 轮
        - `include_tool_calls`（Phase 2.6 v0.5）：``True`` 时给每条 assistant
          消息附上 ``tool_calls`` 字段，从 ``AgentV2ToolCall`` 反查出一组
          ``{id, tool_name, args, result, error, status, duration_ms}`` 条目。
          Runner 用这份数据把工具往返过程渲染进 ``<conversation-history>``，
          让追问"刚才那个 kb_audit 结果"时不用重跑。
        """
        q = cls.model.select().where(
            cls.model.session_id == session_id,
            cls.model.role.in_(["user", "assistant"]),
        )
        if exclude_message_id:
            q = q.where(cls.model.id != exclude_message_id)
        if since_create_time:
            q = q.where(cls.model.create_time > since_create_time)

        # 先按 create_time 降序取 limit 条，再翻回升序，这样拿的是"最新的 N 条"
        rows = list(q.order_by(cls.model.create_time.desc()).limit(limit).dicts())
        rows.reverse()

        if not include_tool_calls or not rows:
            return rows

        # 收集所有 assistant 消息里引用的 tool_call_ids，一次查完再按 message_id 归位
        wanted_ids: list[str] = []
        for r in rows:
            if r.get("role") == "assistant":
                ids = r.get("tool_call_ids") or []
                if isinstance(ids, list):
                    wanted_ids.extend(str(x) for x in ids if x)
        if not wanted_ids:
            for r in rows:
                r["tool_calls"] = []
            return rows

        tool_rows = list(
            AgentV2ToolCall.select(
                AgentV2ToolCall.id,
                AgentV2ToolCall.message_id,
                AgentV2ToolCall.tool_name,
                AgentV2ToolCall.args,
                AgentV2ToolCall.result,
                AgentV2ToolCall.error,
                AgentV2ToolCall.status,
                AgentV2ToolCall.duration_ms,
                AgentV2ToolCall.start_time,
            )
            .where(AgentV2ToolCall.id.in_(wanted_ids))
            .dicts()
        )
        # 按原 tool_call_ids 顺序归位（保留 Agent 实际调用顺序）
        by_id = {t["id"]: t for t in tool_rows}
        for r in rows:
            if r.get("role") != "assistant":
                r["tool_calls"] = []
                continue
            ordered = []
            for tid in r.get("tool_call_ids") or []:
                t = by_id.get(str(tid))
                if t:
                    ordered.append(t)
            r["tool_calls"] = ordered
        return rows


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


# ────────────────────────────── Attachment (Phase 2.7) ──────────────────────────────


# 默认 staged 附件 TTL — 24 小时；cron 扫过期行清 MinIO blob + DB row。
# 对齐 PLAN-attachments.md §7 "Safety limits" / §11 决策 log。
_ATTACHMENT_DEFAULT_TTL_MS = 24 * 60 * 60 * 1000


class AgentV2AttachmentService(CommonService):
    model = AgentV2Attachment

    # ─────────── write paths ───────────

    @classmethod
    @DB.connection_context()
    def create_staged(
        cls,
        *,
        session_id: str,
        tenant_id: str,
        uploaded_by: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        hash_xxh128: str,
        blob_path: str,
        origin: str = "upload",
        source_url: str | None = None,
        preview_text: str | None = None,
        ttl_ms: int = _ATTACHMENT_DEFAULT_TTL_MS,
    ) -> AgentV2Attachment:
        """Insert a new attachment row in status='staged'.

        Caller is responsible for uploading the blob to MinIO **before** this
        call — on DB failure the blob becomes orphan; the cron sweeper (not
        yet wired) will GC orphans by scanning MinIO vs DB.
        """
        if not all([session_id, tenant_id, uploaded_by, filename, blob_path]):
            raise ValueError(
                "session_id / tenant_id / uploaded_by / filename / blob_path "
                "are all required"
            )
        if origin not in ("upload", "web_fetch", "agent_generated"):
            raise ValueError(f"invalid origin={origin!r}")
        if size_bytes < 0:
            raise ValueError(f"negative size_bytes={size_bytes}")

        now = current_timestamp()
        row = cls.model.create(
            id=get_uuid(),
            session_id=session_id,
            tenant_id=tenant_id,
            uploaded_by=uploaded_by,
            filename=filename[:255],
            mime_type=mime_type[:100],
            size_bytes=size_bytes,
            hash_xxh128=hash_xxh128[:32],
            blob_path=blob_path[:500],
            origin=origin,
            source_url=(source_url or "")[:2048] if source_url else None,
            preview_text=preview_text or "",
            status="staged",
            expires_at=now + ttl_ms if ttl_ms > 0 else None,
            **_now_meta(),
        )
        return row

    @classmethod
    @DB.connection_context()
    def mark_archived(
        cls,
        *,
        attachment_id: str,
        doc_id: str,
        kb_id: str,
    ) -> bool:
        """Flip status → archived + record target doc / kb. Idempotent: calling
        twice with the same doc_id is a noop (returns True)."""
        row = cls.model.select().where(cls.model.id == attachment_id).first()
        if not row:
            return False
        if row.status == "archived" and row.archived_doc_id == doc_id:
            return True
        row.status = "archived"
        row.archived_doc_id = doc_id
        row.archived_kb_id = kb_id
        row.archived_at = current_timestamp()
        row.expires_at = None  # archived rows never expire
        row.update_time = current_timestamp()
        row.update_date = datetime_format(datetime.now())
        row.save()
        return True

    @classmethod
    @DB.connection_context()
    def reject(cls, *, attachment_id: str) -> bool:
        """User-initiated rejection; cron will GC the blob. Returns False if
        row not found or already terminal (archived/rejected/expired)."""
        row = cls.model.select().where(cls.model.id == attachment_id).first()
        if not row:
            return False
        if row.status in ("archived", "rejected", "expired"):
            return False
        row.status = "rejected"
        row.update_time = current_timestamp()
        row.update_date = datetime_format(datetime.now())
        row.save()
        return True

    @classmethod
    @DB.connection_context()
    def mark_expired_stale(cls, *, now_ms: int | None = None) -> int:
        """Cron entry — flip all staged rows past expires_at to status=expired.
        Returns count flipped. Caller should separately GC the MinIO blobs
        (walking expired rows)."""
        cutoff = now_ms if now_ms is not None else current_timestamp()
        q = cls.model.update(
            status="expired",
            update_time=current_timestamp(),
            update_date=datetime_format(datetime.now()),
        ).where(
            (cls.model.status == "staged")
            & (cls.model.expires_at.is_null(False))
            & (cls.model.expires_at < cutoff)
        )
        return q.execute()

    # ─────────── blob GC ───────────

    @classmethod
    @DB.connection_context()
    def list_blob_gc_candidates(
        cls, *, limit: int = 500, rejected_grace_ms: int = 7 * 24 * 3600 * 1000,
        now_ms: int | None = None,
    ) -> list:
        """Rows whose MinIO blob should be reclaimed:

        - ``status=expired`` and ``blob_path`` still set (no grace — the row
          already lived 24 h while staged)
        - ``status=rejected`` and ``update_time < now - rejected_grace_ms``

        Returns at most ``limit`` rows; caller deletes the blob then calls
        ``mark_blob_reclaimed`` to null out ``blob_path`` so we don't pick the
        row up again.
        """
        cutoff = (now_ms if now_ms is not None else current_timestamp()) - rejected_grace_ms
        return list(
            cls.model.select()
            .where(
                cls.model.blob_path.is_null(False)
                & (
                    (cls.model.status == "expired")
                    | (
                        (cls.model.status == "rejected")
                        & (cls.model.update_time < cutoff)
                    )
                )
            )
            .order_by(cls.model.update_time.asc())
            .limit(limit)
        )

    @classmethod
    @DB.connection_context()
    def mark_blob_reclaimed(cls, attachment_id: str) -> bool:
        """Null out ``blob_path`` so the sweeper stops picking the row up.
        The row itself is preserved as an audit tombstone."""
        row = cls.model.select().where(cls.model.id == attachment_id).first()
        if not row:
            return False
        row.blob_path = None
        row.update_time = current_timestamp()
        row.update_date = datetime_format(datetime.now())
        row.save()
        return True

    # ─────────── read paths ───────────

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, attachment_id: str) -> AgentV2Attachment | None:
        return cls.model.select().where(cls.model.id == attachment_id).first()

    @classmethod
    @DB.connection_context()
    def find_by_hash(
        cls, *, tenant_id: str, hash_xxh128: str, reusable_statuses: tuple[str, ...] = ("staged", "archived")
    ) -> AgentV2Attachment | None:
        """Tenant-scoped hash lookup for dedupe. Matches staged (= pending
        user approval) and archived (= already in KB, same content should not
        re-upload). Rejected / expired rows are NOT dedupe candidates."""
        return (
            cls.model.select()
            .where(
                (cls.model.tenant_id == tenant_id)
                & (cls.model.hash_xxh128 == hash_xxh128)
                & (cls.model.status.in_(list(reusable_statuses)))
            )
            .order_by(cls.model.create_time.desc())
            .first()
        )

    @classmethod
    @DB.connection_context()
    def find_by_source_url(
        cls, *, tenant_id: str, source_url: str, ttl_ms: int = 24 * 60 * 60 * 1000
    ) -> AgentV2Attachment | None:
        """Dedupe per tenant + URL within TTL window. Used by
        `web_fetch_to_attachment` to avoid re-downloading the same URL."""
        cutoff = current_timestamp() - ttl_ms
        return (
            cls.model.select()
            .where(
                (cls.model.tenant_id == tenant_id)
                & (cls.model.source_url == source_url)
                & (cls.model.status.in_(["staged", "archived"]))
                & (cls.model.create_time >= cutoff)
            )
            .order_by(cls.model.create_time.desc())
            .first()
        )

    @classmethod
    @DB.connection_context()
    def list_by_session(
        cls,
        *,
        session_id: str,
        statuses: tuple[str, ...] | None = ("staged", "archived"),
        limit: int = 50,
    ) -> list[AgentV2Attachment]:
        """Return attachments for a session, default excluding rejected /
        expired which are cleanup states. Orders by create_time asc so the
        UI reads chronologically."""
        q = cls.model.select().where(cls.model.session_id == session_id)
        if statuses:
            q = q.where(cls.model.status.in_(list(statuses)))
        return list(q.order_by(cls.model.create_time.asc()).limit(limit))

    @classmethod
    @DB.connection_context()
    def count_staged_for_session(cls, session_id: str) -> int:
        return (
            cls.model.select()
            .where(
                (cls.model.session_id == session_id)
                & (cls.model.status == "staged")
            )
            .count()
        )

    @classmethod
    @DB.connection_context()
    def total_staged_bytes_for_session(cls, session_id: str) -> int:
        from peewee import fn

        row = (
            cls.model.select(fn.COALESCE(fn.SUM(cls.model.size_bytes), 0).alias("total"))
            .where(
                (cls.model.session_id == session_id)
                & (cls.model.status == "staged")
            )
            .dicts()
            .first()
        )
        return int(row.get("total") or 0) if row else 0
