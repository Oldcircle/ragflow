"""rag_list_docs 工具 — 列出知识库中的所有文档元数据。

让 Agent 了解"知识库里都有哪些文档"，以便后续选择文档做精确检索或读全文。
"""

from __future__ import annotations

import asyncio
import logging

from api.db.services.document_service import DocumentService

from .base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.rag_list_docs")


@tool(
    name="rag_list_docs",
    description=(
        "Use this tool when you need to know which documents exist in the "
        "session's knowledge base(s) before deciding what to retrieve or "
        "read. Also appropriate when the user asks 'what files are in this "
        "KB?'\n\n"
        "Returns documents across all KBs in scope as a merged list with "
        "ID, name, file type, chunk count, and parse progress.\n\n"
        "Usage notes:\n"
        "- Supply `keywords` to filter by filename substring (case-insensitive).\n"
        "- Use `rag_retrieve` (not this tool) for answering content questions; "
        "this tool returns *metadata only*."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": (
                    "Optional filename substring filter "
                    "(case-insensitive). Leave empty for all documents."
                ),
            },
            "page": {"type": "integer", "default": 1, "minimum": 1},
            "page_size": {
                "type": "integer",
                "default": 50,
                "minimum": 1,
                "maximum": 200,
                "description": (
                    "Max rows per KB to return. Default 50."
                ),
            },
        },
    },
)
async def rag_list_docs(args: dict) -> dict:
    ctx = get_ctx()
    kb_ids = list(ctx.kb_ids)
    keywords = str(args.get("keywords", "")).strip() or None
    page = int(args.get("page", 1))
    page_size = int(args.get("page_size", 50))

    logger.info(
        "rag_list_docs: kb_ids=%s keywords=%r page=%d size=%d",
        kb_ids,
        keywords,
        page,
        page_size,
    )

    def _query() -> tuple[list[dict], int]:
        all_docs: list[dict] = []
        grand_total = 0
        for kb_id in kb_ids:
            docs_list, count = DocumentService.get_list(
                kb_id=kb_id,
                page_number=page,
                items_per_page=page_size,
                orderby="create_time",
                desc=True,
                keywords=keywords,
                id=None,
                name=None,
            )
            grand_total += count
            for d in docs_list:
                all_docs.append(
                    {
                        "doc_id": d.get("id"),
                        "kb_id": d.get("kb_id"),
                        "name": d.get("name"),
                        "type": d.get("type"),
                        "suffix": d.get("suffix"),
                        "size": d.get("size"),
                        "chunk_num": d.get("chunk_num", 0),
                        "token_num": d.get("token_num", 0),
                        "progress": d.get("progress", 0.0),
                        "parser_id": d.get("parser_id"),
                        "create_date": d.get("create_date"),
                    }
                )
        return all_docs, grand_total

    docs, total = await asyncio.to_thread(_query)
    return mcp_json_response(
        {
            "total_matched": total,
            "returned": len(docs),
            "page": page,
            "page_size": page_size,
            "documents": docs,
        }
    )
