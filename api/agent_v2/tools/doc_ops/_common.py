"""doc_ops 写工具共享基础设施（Phase 2.6）。

提供三件事：

1. ``require_kb_write(action, min_role, kb_id_from)`` — 工具装饰器
   - 前置 RBAC 检查，失败直接返 ``{"error": "no_access", ...}`` 并写 deny 审计
   - 成功执行后自动写 allow 审计（含 metadata 中的可逆线索）
   - 异常写 deny 审计 + 重抛（Runner 翻成 error event）

2. ``write_audit(action, kb_id, resource_id, result, reason, metadata)`` —
   直写审计的轻量入口，工具内部做"部分成功"记录时用

3. ``check_idempotency(key, ttl_seconds)`` + ``remember_result(key, result)`` —
   防重复执行；Redis 优先、内存兜底（沿用 P2.2 dedup / P3.1c rate_limit 模式）

**审计 action 命名规范**：`kb.doc.<op>` / `kb.<op>`，与 P2.1 审计命名保持一致。
**metadata 规范**：``{"op", "doc_id"?, "kb_id"?, "source_kb_id"?, "target_kb_id"?,
"reversible_hint"?, ... tool-specific ...}``。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from ..base import ToolContext, get_ctx, mcp_json_response

logger = logging.getLogger("ragflow.agent_v2.doc_ops.common")


# ────────────────────────────── RBAC / Audit 装饰器 ──────────────────────────────


def _default_kb_from_args(args: dict) -> str | None:
    """默认从 args 里提取 ``kb_id``；派生工具可通过 ``kb_id_from`` 覆盖。"""
    return args.get("kb_id")


def require_kb_write(
    *,
    action: str,
    min_role: str = "contributor",
    kb_id_from: Callable[[dict], str | None] = _default_kb_from_args,
    extra_audit_metadata: Callable[[dict, Any, ToolContext], dict] | None = None,
):
    """所有 doc_ops 写工具共享的装饰器。

    - ``action``：审计里的 action 字段，比如 ``kb.doc.tag``
    - ``min_role``：需要的最小 KB 角色；默认 CONTRIBUTOR
    - ``kb_id_from``：从 MCP 入参 dict 里取 kb_id 的 callable；多 kb 场景
      （如 doc_archive 的 source/target）在工具内部**额外**校验
    - ``extra_audit_metadata``：工具返回后把结构化补充信息塞进 audit metadata

    被装饰的函数签名固定为 ``async def tool(args: dict) -> dict``（MCP 工具协议）。
    """

    from api.db.services.audit_log_service import AuditLogService
    from api.db.services.dataset_access_service import (
        AccessDeniedError,
        DatasetAccessService,
        DatasetRole,
    )

    role_enum = DatasetRole(min_role)

    def decorator(fn: Callable[[dict], Awaitable[dict]]) -> Callable[[dict], Awaitable[dict]]:
        @functools.wraps(fn)
        async def wrapper(args: dict) -> dict:
            ctx = get_ctx(require=["tenant_id"])
            user_id = ctx.user_id
            tenant_id = ctx.tenant_id
            kb_id = kb_id_from(args)

            # 1. RBAC gate（kb_id 提供 + user_id 提供时）
            if kb_id and user_id:
                try:
                    DatasetAccessService.require_at_least(kb_id, user_id, role_enum)
                except AccessDeniedError as e:
                    _safe_audit(
                        AuditLogService,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        action=action,
                        resource_type="knowledgebase",
                        resource_id=kb_id,
                        result="deny",
                        reason="insufficient_role",
                        metadata={
                            "op": action,
                            "required": e.required,
                            "actual": e.actual,
                        },
                    )
                    return mcp_json_response({
                        "error": "no_access",
                        "message": (
                            f"Need at least {min_role} on {kb_id} to perform "
                            f"{action}; current role = {e.actual or 'none'}."
                        ),
                    })

            # 2. 执行工具
            started = time.time()
            try:
                result = await fn(args)
            except Exception as exc:  # noqa: BLE001
                _safe_audit(
                    AuditLogService,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    action=action,
                    resource_type="knowledgebase",
                    resource_id=kb_id,
                    result="deny",
                    reason=f"{type(exc).__name__}: {str(exc)[:120]}",
                    metadata={
                        "op": action,
                        "duration_ms": int((time.time() - started) * 1000),
                    },
                )
                logger.exception("doc_ops tool %s failed", action)
                raise

            # 3. 成功审计（含可选补充 metadata）
            meta: dict = {
                "op": action,
                "duration_ms": int((time.time() - started) * 1000),
            }
            if kb_id:
                meta["kb_id"] = kb_id
            if extra_audit_metadata:
                try:
                    extra = extra_audit_metadata(args, result, ctx)
                    if isinstance(extra, dict):
                        meta.update(extra)
                except Exception:
                    logger.exception("extra_audit_metadata callback failed")

            _safe_audit(
                AuditLogService,
                user_id=user_id,
                tenant_id=tenant_id,
                action=action,
                resource_type="knowledgebase",
                resource_id=kb_id,
                result="allow",
                reason=_extract_op_status(result),
                metadata=meta,
            )
            return result

        # 标记装饰过，便于测试 introspection
        wrapper.__doc_ops_write__ = True  # type: ignore[attr-defined]
        wrapper.__doc_ops_action__ = action  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _extract_op_status(result: Any) -> str:
    """从 MCP response 里提一句 reason（给审计 UI 里 reason 列用）。

    响应体是 ``{"content":[{"type":"text","text":"{json}"}]}`` 或已解构的 dict。
    """
    try:
        if isinstance(result, dict):
            # MCP envelope
            content = result.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict):
                    inner = first.get("text")
                    if isinstance(inner, str):
                        parsed = json.loads(inner)
                        if isinstance(parsed, dict):
                            return parsed.get("status") or parsed.get("error") or "ok"
            # 非 envelope，直接读
            return result.get("status") or result.get("error") or "ok"
    except Exception:
        pass
    return "ok"


def _safe_audit(svc, **kw) -> None:
    """audit 写挂了不应影响主流程。"""
    try:
        svc.log(**kw)
    except Exception:
        logger.exception("audit log write failed for %s", kw.get("action"))


# ────────────────────────────── Idempotency ──────────────────────────────


_IDEM_PREFIX = "agent_v2:doc_ops:idem:"
_IDEM_TTL_DEFAULT = 90  # 秒
_IDEM_MEM: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_IDEM_MEM_CAP = 2048
_IDEM_LOCK = asyncio.Lock()


def _hash_call(action: str, args: dict, caller_id: str) -> str:
    """对 (action, caller_id, canonical(args) minus idempotency_key) 做哈希。

    只用确定性 JSON（sort_keys）+ sha256，避免 args 顺序变化带来误差。
    """
    scrub = {k: v for k, v in args.items() if k != "idempotency_key"}
    canon = json.dumps(scrub, sort_keys=True, ensure_ascii=False, default=str)
    raw = f"{action}|{caller_id}|{canon}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def check_idempotency(
    *,
    action: str,
    args: dict,
    caller_id: str,
    ttl_seconds: int = _IDEM_TTL_DEFAULT,
) -> dict | None:
    """若当前调用是近期重放（相同 (action, caller, args) 哈希） → 返回缓存结果。

    参数 ``args.idempotency_key`` 里若显式给了 key，则优先用它做缓存键。
    """
    raw_key = args.get("idempotency_key")
    if raw_key:
        key_part = str(raw_key)
    else:
        key_part = _hash_call(action, args, caller_id)
    full_key = _IDEM_PREFIX + key_part

    # Redis 优先
    cached = _redis_get(full_key)
    if cached is not None:
        return cached

    # 内存兜底
    async with _IDEM_LOCK:
        now = time.time()
        # 过期清理
        for k in list(_IDEM_MEM.keys())[:64]:
            ts, _ = _IDEM_MEM[k]
            if now - ts > ttl_seconds:
                _IDEM_MEM.pop(k, None)
        v = _IDEM_MEM.get(full_key)
        if v is None:
            return None
        ts, payload = v
        if now - ts > ttl_seconds:
            _IDEM_MEM.pop(full_key, None)
            return None
        return payload


async def remember_result(
    *,
    action: str,
    args: dict,
    caller_id: str,
    result: dict,
    ttl_seconds: int = _IDEM_TTL_DEFAULT,
) -> None:
    raw_key = args.get("idempotency_key")
    key_part = str(raw_key) if raw_key else _hash_call(action, args, caller_id)
    full_key = _IDEM_PREFIX + key_part

    if _redis_set(full_key, result, ttl_seconds):
        return

    async with _IDEM_LOCK:
        if len(_IDEM_MEM) > _IDEM_MEM_CAP:
            for k in list(_IDEM_MEM.keys())[:128]:
                _IDEM_MEM.pop(k, None)
        _IDEM_MEM[full_key] = (time.time(), result)


def _redis_get(key: str) -> dict | None:
    try:
        from rag.utils.redis_conn import REDIS_CONN
    except Exception:
        return None
    client = getattr(REDIS_CONN, "REDIS", None)
    if client is None:
        return None
    try:
        raw = client.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return json.loads(raw)
    except Exception:
        return None


def _redis_set(key: str, value: dict, ttl: int) -> bool:
    try:
        from rag.utils.redis_conn import REDIS_CONN
    except Exception:
        return False
    client = getattr(REDIS_CONN, "REDIS", None)
    if client is None:
        return False
    try:
        client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
        return True
    except Exception:
        return False


# ────────────────────────────── 统一 ops 响应格式 ──────────────────────────────


def ok(**kw) -> dict:
    """工具成功响应：``{"status":"ok", ...}``。"""
    return mcp_json_response({"status": "ok", **kw})


def err(code: str, message: str, **kw) -> dict:
    """工具失败响应：``{"error":<code>, "message":..., ...}``。

    用于"非致命"的业务拒绝（例如 kb_id 不存在），不抛异常。RBAC 的 deny 由
    装饰器自己处理，不走这个入口。
    """
    return mcp_json_response({"error": code, "message": message, **kw})


__all__ = [
    "require_kb_write",
    "check_idempotency",
    "remember_result",
    "ok",
    "err",
]
