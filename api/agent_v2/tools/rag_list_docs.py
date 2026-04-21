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
        "列出当前会话可见知识库下的文档（元数据：ID、名称、类型、chunk 数、进度）。"
        "适用于用户问「有哪些文件」或 Agent 需要先了解文档清单再决定下一步查哪份时使用。"
        "返回所有 KB 的合并列表，可选按文件名关键词过滤。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "按文件名模糊过滤（可选）。例如「管理办法」只匹配文件名含此词的",
            },
            "page": {"type": "integer", "default": 1, "minimum": 1},
            "page_size": {
                "type": "integer",
                "default": 50,
                "minimum": 1,
                "maximum": 200,
                "description": "单 KB 返回条数上限，默认 50",
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
