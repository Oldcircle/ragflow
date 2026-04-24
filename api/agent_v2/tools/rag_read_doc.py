"""rag_read_doc 工具 — 读取指定文档的完整（或分段）内容。

当语义检索只返回碎片，Agent 判断需要读某份文档的完整上下文时调用。
通过 Dealer.chunk_list() 按 chunk_order_int 顺序从 ES/Infinity 拉取文本。
"""

from __future__ import annotations

import asyncio
import logging

from api.db.services.document_service import DocumentService
from common import settings

from .base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.rag_read_doc")


@tool(
    name="rag_read_doc",
    description=(
        "Use this tool when retrieved chunks are fragmented or missing "
        "context and you need the full body of a specific document to "
        "answer confidently. First resolve the doc_id via `rag_retrieve` "
        "or `rag_list_docs`.\n\n"
        "Returns concatenated chunk text with metadata. Output is capped "
        "at ~32 KB — use `chunk_offset` + `chunk_limit` for longer "
        "documents.\n\n"
        "Usage notes:\n"
        "- Don't use this as a substitute for `rag_retrieve`; retrieval-"
        "by-similarity is cheaper and more focused.\n"
        "- Cite [N] markers as if this document were a single chunk source."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": (
                    "Document ID from rag_retrieve or rag_list_docs."
                ),
            },
            "chunk_offset": {
                "type": "integer",
                "default": 0,
                "minimum": 0,
                "description": "Start chunk index (0 = from beginning).",
            },
            "chunk_limit": {
                "type": "integer",
                "default": 50,
                "minimum": 1,
                "maximum": 500,
                "description": "Number of chunks to return. Default 50.",
            },
        },
        "required": ["doc_id"],
    },
)
async def rag_read_doc(args: dict) -> dict:
    ctx = get_ctx()
    kb_ids = list(ctx.kb_ids)

    doc_id = str(args.get("doc_id", "")).strip()
    if not doc_id:
        return mcp_json_response({"error": "doc_id 不能为空"})
    offset = max(0, int(args.get("chunk_offset", 0)))
    limit = min(500, max(1, int(args.get("chunk_limit", 50))))

    # 找到文档所属的 KB / tenant（避免暴露不属于本会话 kb_ids 的文档）
    def _locate() -> tuple[str, str, str, int] | None:
        """Return (kb_id, tenant_id, doc_name, chunk_num) or None if not found/allowed."""
        for kb_id in kb_ids:
            docs, _ = DocumentService.get_list(
                kb_id=kb_id,
                page_number=1,
                items_per_page=1,
                orderby="create_time",
                desc=True,
                keywords=None,
                id=doc_id,
                name=None,
            )
            if docs:
                from api.db.services.knowledgebase_service import KnowledgebaseService

                kbs = KnowledgebaseService.get_by_ids([kb_id])
                tenant_id = kbs[0].tenant_id if kbs else None
                if not tenant_id:
                    return None
                doc = docs[0]
                return (kb_id, tenant_id, doc.get("name", ""), doc.get("chunk_num", 0))
        return None

    located = await asyncio.to_thread(_locate)
    if not located:
        return mcp_json_response(
            {"error": f"doc_id={doc_id!r} 不存在于当前会话授权的知识库中"}
        )
    kb_id, tenant_id, doc_name, chunk_num = located

    logger.info(
        "rag_read_doc: doc_id=%s kb_id=%s tenant=%s offset=%d limit=%d",
        doc_id,
        kb_id,
        tenant_id,
        offset,
        limit,
    )

    def _fetch_chunks():
        # Dealer.chunk_list 是同步方法
        return settings.retriever.chunk_list(
            doc_id=doc_id,
            tenant_id=tenant_id,
            kb_ids=[kb_id],
            max_count=offset + limit,  # 先取前 offset+limit 个
            fields=[
                "doc_id",
                "docnm_kwd",
                "chunk_order_int",
                "page_num_int",
                "content_with_weight",
            ],
            sort_by_position=False,  # 按 chunk_order_int 排
        )

    chunks_raw = await asyncio.to_thread(_fetch_chunks)
    # 手动应用 offset（chunk_list 不直接支持 offset，先 fetch 再切片）
    chunks_sliced = chunks_raw[offset : offset + limit]
    chunks_out = [
        {
            "order": c.get("chunk_order_int"),
            "page": (c.get("page_num_int") or [None])[0],
            "content": c.get("content_with_weight", ""),
        }
        for c in chunks_sliced
    ]
    return mcp_json_response(
        {
            "doc_id": doc_id,
            "doc_name": doc_name,
            "kb_id": kb_id,
            "total_chunks": chunk_num,
            "returned_chunks": len(chunks_out),
            "chunk_offset": offset,
            "chunks": chunks_out,
        }
    )
