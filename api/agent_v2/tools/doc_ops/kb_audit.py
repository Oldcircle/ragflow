"""kb_audit — 结构化 KB 体检（Phase 2.6 v0.2）。

让 Agent 扫一次 KB 知道"什么该动"：未解析 / 解析失败 / 陈旧 / 重复 / 标签
分布。读-only，走 VIEWER+ 权限。

输出设计要点：
- 不返全量文档 ID 列表（可能几千条）；每类只返最多 ``sample_limit`` 个样本
- 所有"是否陈旧 / 是否重复"的判断在 SQL 里做，不把 blob 拉回来
- 响应尺寸 < 32KB（默认上限，足够 Agent 做决策）
"""

from __future__ import annotations

import logging
import time

from ..base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.doc_ops.kb_audit")

_DEFAULT_STALE_DAYS = 180
_DEFAULT_SAMPLE_LIMIT = 5
_MAX_SAMPLE_LIMIT = 30


@tool(
    name="kb_audit",
    description=(
        "Use this tool when you need a complete structural health check of "
        "a KB — before planning a cleanup, before writing a report, or when "
        "the user asks 'is this KB healthy?' / 'what needs attention?'.\n\n"
        "Returns a structured report: doc counts by parse status (done / "
        "running / queued / cancelled / likely_failed), stale documents "
        "(older than `stale_days`, default 180), duplicate candidates "
        "(same content_hash), unparsed docs (progress<1), top tags, and "
        "totals. Also returns a human-readable `suggestions` field you "
        "can lean on when writing follow-up recommendations.\n\n"
        "Read-only. Requires VIEWER+."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kb_id": {
                "type": "string",
                "description": "KB ID to audit.",
            },
            "stale_days": {
                "type": "integer",
                "default": _DEFAULT_STALE_DAYS,
                "minimum": 7,
                "maximum": 3650,
                "description": (
                    "Documents untouched for this many days are flagged "
                    "as stale. Default 180."
                ),
            },
            "sample_limit": {
                "type": "integer",
                "default": _DEFAULT_SAMPLE_LIMIT,
                "minimum": 1,
                "maximum": _MAX_SAMPLE_LIMIT,
                "description": (
                    "Number of sample documents returned per issue class "
                    "(name + ID). Default 5, max 30."
                ),
            },
        },
        "required": ["kb_id"],
    },
)
async def kb_audit(args: dict) -> dict:
    ctx = get_ctx(require=["tenant_id"])
    tenant_id = ctx.tenant_id
    user_id = ctx.user_id
    kb_id = str(args.get("kb_id") or "").strip()
    stale_days = int(args.get("stale_days") or _DEFAULT_STALE_DAYS)
    sample_limit = max(1, min(int(args.get("sample_limit") or _DEFAULT_SAMPLE_LIMIT),
                              _MAX_SAMPLE_LIMIT))

    if not kb_id:
        return mcp_json_response({"error": "invalid_input", "message": "kb_id required"})

    from api.db.services.dataset_access_service import (
        AccessDeniedError,
        DatasetAccessService,
        DatasetRole,
    )
    from api.db.services.knowledgebase_service import KnowledgebaseService

    # VIEWER+ 读权限
    if user_id:
        try:
            DatasetAccessService.require_at_least(kb_id, user_id, DatasetRole.VIEWER)
        except AccessDeniedError as e:
            return mcp_json_response({
                "error": "no_access",
                "message": f"Need viewer+ on {kb_id}: {e.actual or 'none'}",
            })

    k_ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not k_ok or not kb:
        return mcp_json_response({"error": "not_found", "message": f"KB {kb_id!r} not found"})
    if kb.tenant_id != tenant_id:
        return mcp_json_response({"error": "out_of_scope", "message": "KB not in current tenant"})

    # 做查询
    from api.db.db_models import Document

    now_ms = int(time.time() * 1000)
    stale_cutoff_ms = now_ms - stale_days * 86400 * 1000

    try:
        total_docs = (
            Document.select().where(Document.kb_id == kb_id).count()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("kb_audit: count query failed")
        return mcp_json_response({
            "error": "storage_error",
            "message": f"{type(exc).__name__}: {exc}",
        })

    # 按 progress + run 状态拆分
    # run=1: 正在处理 / run=2: cancelled / run=0: idle
    # progress 1.0: done；0.0 且 run=0：queued；0.0 且 run=1：running；<1 但 run=2：cancelled
    by_status = _count_by_status(kb_id)

    # 陈旧文档
    stale_q = (
        Document.select(Document.id, Document.name, Document.create_time)
        .where(
            (Document.kb_id == kb_id)
            & (Document.create_time < stale_cutoff_ms)
        )
        .order_by(Document.create_time.asc())
        .limit(sample_limit)
    )
    stale_docs = [
        {"doc_id": d.id, "doc_name": d.name, "create_time": d.create_time}
        for d in stale_q
    ]
    stale_total = (
        Document.select()
        .where(
            (Document.kb_id == kb_id)
            & (Document.create_time < stale_cutoff_ms)
        )
        .count()
    )

    # 重复候选（同 content_hash 出现 ≥ 2 次）
    dup_samples = _find_duplicates(kb_id, sample_limit)

    # 未解析（progress < 1.0）
    unparsed_q = (
        Document.select(Document.id, Document.name, Document.progress, Document.run)
        .where(
            (Document.kb_id == kb_id)
            & (Document.progress < 1.0)
        )
        .order_by(Document.create_time.desc())
        .limit(sample_limit)
    )
    unparsed_docs = [
        {
            "doc_id": d.id, "doc_name": d.name,
            "progress": float(d.progress or 0.0), "run": d.run,
        }
        for d in unparsed_q
    ]
    unparsed_total = (
        Document.select()
        .where((Document.kb_id == kb_id) & (Document.progress < 1.0))
        .count()
    )

    # Top tags
    top_tags = _top_tags_in_kb(kb_id, limit=10)

    # 整体统计
    totals = {
        "doc_num": int(kb.doc_num or 0),
        "chunk_num": int(kb.chunk_num or 0),
        "token_num": int(kb.token_num or 0),
        "embd_id": kb.embd_id,
        "parser_id": kb.parser_id,
    }

    return mcp_json_response({
        "status": "ok",
        "kb_id": kb_id,
        "kb_name": kb.name,
        "audited_at_ms": now_ms,
        "total_docs": total_docs,
        "by_parse_status": by_status,
        "stale": {
            "threshold_days": stale_days,
            "count": stale_total,
            "samples": stale_docs,
        },
        "duplicates": {
            "groups_count": dup_samples.get("groups_count", 0),
            "samples": dup_samples.get("samples", []),
        },
        "unparsed": {
            "count": unparsed_total,
            "samples": unparsed_docs,
        },
        "top_tags": top_tags,
        "totals": totals,
        "suggestions": _derive_suggestions(
            by_status, stale_total, dup_samples, unparsed_total,
        ),
    })


def _count_by_status(kb_id: str) -> dict:
    from api.db.db_models import Document

    try:
        base = Document.select().where(Document.kb_id == kb_id)
        done = base.where(Document.progress >= 1.0).count()
        # run 字段为字符串："0" idle / "1" running / "2" cancelled
        running = base.where(
            (Document.progress < 1.0) & (Document.run == "1")
        ).count()
        cancelled = base.where(Document.run == "2").count()
        queued = base.where(
            (Document.progress < 1.0)
            & ((Document.run == "0") | (Document.run.is_null(True)))
        ).count()
        # Failed heuristic: progress > 0 but < 1 且非 running 非 cancelled
        # （RAGFlow 没独立 failed 字段；用 progress_msg 的"failed"检索更准）
        failed = base.where(Document.progress_msg.contains("fail")).count()
        return {
            "done": done,
            "running": running,
            "queued": queued,
            "cancelled": cancelled,
            "likely_failed": failed,
        }
    except Exception:
        logger.exception("kb_audit: status count failed")
        return {"done": -1}


def _find_duplicates(kb_id: str, sample_limit: int) -> dict:
    """按 content_hash group by，取重复 ≥2 的样本。"""
    from api.db.db_models import DB, Document
    from peewee import fn

    try:
        with DB.connection_context():
            groups = list(
                Document.select(
                    Document.content_hash,
                    fn.COUNT(Document.id).alias("cnt"),
                )
                .where(
                    (Document.kb_id == kb_id)
                    & (Document.content_hash != "")
                    & (Document.content_hash.is_null(False))
                )
                .group_by(Document.content_hash)
                .having(fn.COUNT(Document.id) > 1)
                .limit(sample_limit)
                .dicts()
            )
    except Exception:
        logger.exception("kb_audit: duplicate scan failed")
        return {"groups_count": 0, "samples": []}

    if not groups:
        return {"groups_count": 0, "samples": []}

    samples = []
    for g in groups:
        h = g["content_hash"]
        cnt = g["cnt"]
        # 每 hash 最多拉 3 个文档
        docs = list(
            Document.select(Document.id, Document.name, Document.create_time)
            .where((Document.kb_id == kb_id) & (Document.content_hash == h))
            .limit(3)
            .dicts()
        )
        samples.append({
            "content_hash": h,
            "duplicate_count": int(cnt),
            "docs": docs,
        })
    return {"groups_count": len(groups), "samples": samples}


def _top_tags_in_kb(kb_id: str, limit: int = 10) -> list[dict]:
    """Top-N tags by doc count — 从 DocMetadataService 读 ES / Infinity。

    失败时返空 list（tags 是 ES-backed，可能不可用）。
    """
    try:
        from api.db.services.doc_metadata_service import DocMetadataService

        summary = DocMetadataService.get_metadata_summary(kb_id) or {}
        tags_bucket = summary.get("tags") or {}
        if not isinstance(tags_bucket, dict):
            return []
        items = [(k, len(v) if isinstance(v, list) else int(v))
                 for k, v in tags_bucket.items()]
        items.sort(key=lambda x: -x[1])
        return [{"tag": k, "doc_count": c} for k, c in items[:limit]]
    except Exception:
        logger.debug("kb_audit: top_tags unavailable (ES backend missing?)")
        return []


def _derive_suggestions(by_status, stale_total, duplicates, unparsed_total) -> list[str]:
    """给 Agent 一些人类可读的『下一步建议』候选。"""
    s: list[str] = []
    if stale_total > 20:
        s.append(
            f"发现 {stale_total} 份陈旧文档（create_time 超过阈值）——考虑归档到"
            "专门的归档 KB，或直接打 'archived' 标签。"
        )
    dup_groups = duplicates.get("groups_count", 0)
    if dup_groups > 0:
        s.append(
            f"发现 {dup_groups} 组 content_hash 重复——可以保留最早一份并把其余"
            "移走（走 submit_plan 审批）。"
        )
    if unparsed_total > 0 and unparsed_total < 50:
        s.append(
            f"{unparsed_total} 份文档未完成解析——可以选择性 doc_reparse 或等队列消化。"
        )
    likely_failed = by_status.get("likely_failed", 0)
    if likely_failed > 0:
        s.append(
            f"{likely_failed} 份文档 progress_msg 提示失败——建议挨个 "
            "rag_read_doc 查 progress_msg 后决定是重解析还是归档。"
        )
    if not s:
        s.append("KB 状态健康，暂无明显清理项。")
    return s
