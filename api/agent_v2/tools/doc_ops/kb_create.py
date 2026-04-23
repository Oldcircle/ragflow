"""kb_create — 新建一个空知识库（Phase 2.6）。

用途：归档/分类场景里 Agent 要把文档往一个新的"桶"里搬，但桶还不存在。
设计上只允许建**空 KB**；新增文档走 ``doc_upload_from_url`` / `doc_archive``。

权限：调用者必须属于目标 tenant。默认 ``created_by = ctx.user_id``（若无则 tenant_id，
对应 RAGFlow 历史行为）。配额：tenant_quota.kb_max 实时计数，超限硬拒绝。

Audit：``kb.create`` — metadata 里写 name / parser_id / embd_id，便于 revert
（revert = 管理员后台删新建 KB）。
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import get_ctx, tool
from ._common import err, ok, require_kb_write

logger = logging.getLogger("ragflow.agent_v2.doc_ops.kb_create")

_MAX_NAME_LEN = 128


def _extra_audit(args: dict, result: Any, _ctx) -> dict:
    try:
        import json

        if isinstance(result, dict):
            c = result.get("content")
            if isinstance(c, list) and c:
                payload = json.loads(c[0].get("text") or "{}")
                return {
                    "new_kb_id": payload.get("kb_id"),
                    "kb_name": args.get("name"),
                    "parser_id": args.get("parser_id"),
                    "reversible_hint": (
                        "KB is empty; admin can delete via /user-setting or "
                        "KnowledgebaseService.delete_by_id"
                    ),
                }
    except Exception:
        pass
    return {}


@tool(
    name="kb_create",
    description=(
        "新建一个**空**知识库；只在用户要求"
        "『建一个新库用来放 X』、『先建一个归档桶』、『按年份分库』时调用。\n\n"
        "- 不会自动写入任何文档；后续用 doc_archive / doc_upload_from_url 填充。\n"
        "- parser_id / embd_id 不填时自动继承当前会话第一个 KB 的配置（保证 embedding 兼容）。\n"
        "- 失败原因常见：name 重复（会自动加后缀）、kb_max 配额超、tenant 无效。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_NAME_LEN,
                "description": "KB 显示名；同 tenant 内若重名会自动加 -1 / -2 后缀。",
            },
            "description": {
                "type": "string",
                "description": "可选：这个 KB 的用途说明，会写到 KB 详情页。",
            },
            "parser_id": {
                "type": "string",
                "description": (
                    "可选：默认解析器类型，如 'naive' / 'qa' / 'book'。不填 = 继承当前会话第一个 KB。"
                ),
            },
            "embd_id": {
                "type": "string",
                "description": "可选：嵌入模型 ID。不填 = 继承当前会话第一个 KB。",
            },
            "permission": {
                "type": "string",
                "enum": ["me", "team"],
                "default": "me",
                "description": "可见性；'me' 只创建者可见，'team' 同 tenant 成员默认 VIEWER。",
            },
        },
        "required": ["name"],
    },
)
# kb_create 不绑定到既有 kb_id（它是 additive 操作），所以装饰器的 kb_id_from 返回 None，
# RBAC 会跳过 KB 级检查——我们下面用 tenant 级校验 + 配额代替。
@require_kb_write(
    action="kb.create",
    min_role="contributor",
    kb_id_from=lambda _args: None,
    extra_audit_metadata=_extra_audit,
)
async def kb_create(args: dict) -> dict:
    from api.db.services.knowledgebase_service import KnowledgebaseService

    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    name = str(args.get("name") or "").strip()
    if not name:
        return err("invalid_input", "name is required")
    if len(name.encode("utf-8")) > _MAX_NAME_LEN:
        return err("invalid_input", f"name exceeds {_MAX_NAME_LEN} bytes (utf-8)")

    # 配额检查（hard_enforce=1 时硬拒）
    try:
        from api.db.services.tenant_quota_service import (
            QuotaExceeded,
            TenantQuotaService,
        )

        limits = TenantQuotaService.get(tenant_id)
        if limits.hard_enforce:
            current_count = _current_kb_count(tenant_id)
            if current_count >= limits.kb_max:
                raise QuotaExceeded(
                    "kb_max", limits.kb_max, current_count,
                )
    except QuotaExceeded as exc:
        return err(
            "quota_exceeded",
            f"kb_max reached ({exc.used}/{exc.limit}); cannot create more KBs.",
            limit=exc.limit,
            used=exc.used,
        )
    except Exception:
        logger.exception("kb_create: quota check error (proceeding)")

    # 继承当前会话第一个 KB 的 parser / embd 作为默认（保障多 KB 检索时 embedding 一致）
    parser_id = args.get("parser_id")
    embd_id = args.get("embd_id")
    if (not parser_id or not embd_id) and ctx.kb_ids:
        try:
            template_kb = KnowledgebaseService.get_by_id(ctx.kb_ids[0])
            if template_kb and template_kb[0]:
                kb_row = template_kb[1]
                if not parser_id:
                    parser_id = getattr(kb_row, "parser_id", None) or "naive"
                if not embd_id:
                    embd_id = getattr(kb_row, "embd_id", None)
        except Exception:
            logger.debug("kb_create: template KB inherit skipped")
    if not parser_id:
        parser_id = "naive"
    if not embd_id:
        # 最后兜底：询问 tenant 默认 embedding
        try:
            from api.db.services.user_service import TenantService

            _ok, t = TenantService.get_by_id(tenant_id)
            if _ok and t:
                embd_id = getattr(t, "embd_id", None)
        except Exception:
            pass
    if not embd_id:
        return err(
            "no_embd",
            "Could not determine an embedding model; pass embd_id explicitly.",
        )

    description = args.get("description") or ""
    permission = (args.get("permission") or "me").lower()
    if permission not in ("me", "team"):
        return err("invalid_input", f"permission must be 'me' or 'team', got {permission!r}")

    # 通过 create_with_name 构造 payload（复用 RAGFlow 的去重 + 默认注入）
    built_ok, built = KnowledgebaseService.create_with_name(
        name=name,
        tenant_id=tenant_id,
        parser_id=parser_id,
        embd_id=embd_id,
        description=description,
        permission=permission,
    )
    if not built_ok:
        # built 是 get_data_error_result 的响应——提取 message
        msg = _extract_error_message(built)
        return err("invalid_input", msg or "create_with_name rejected")

    # created_by 改成 ctx.user_id（若有），否则保留 tenant_id 兜底
    if ctx.user_id:
        built["created_by"] = ctx.user_id

    try:
        saved = KnowledgebaseService.save(**built)
    except Exception as exc:  # noqa: BLE001
        logger.exception("kb_create save failed: %s", exc)
        return err("storage_error", f"{type(exc).__name__}: {exc}")
    if not saved:
        return err("storage_error", "KnowledgebaseService.save returned False")

    return ok(
        kb_id=built["id"],
        kb_name=built["name"],
        requested_name=name,
        auto_suffixed=(built["name"] != name),
        parser_id=built.get("parser_id"),
        embd_id=built.get("embd_id"),
        permission=permission,
    )


def _current_kb_count(tenant_id: str) -> int:
    from api.db.db_models import Knowledgebase

    try:
        return (
            Knowledgebase.select()
            .where(
                (Knowledgebase.tenant_id == tenant_id) & (Knowledgebase.status == "1")
            )
            .count()
        )
    except Exception:
        return 0


def _extract_error_message(resp: Any) -> str | None:
    if resp is None:
        return None
    try:
        if hasattr(resp, "get_json"):
            payload = resp.get_json()
            if isinstance(payload, dict):
                return payload.get("message") or payload.get("data")
    except Exception:
        pass
    if isinstance(resp, dict):
        return resp.get("message")
    if isinstance(resp, str):
        return resp
    return str(resp)
