"""rag_retrieve 工具 — 在 RAGFlow 知识库中做语义检索。

这是 Agent v2 的核心工具。Agent 遇到需要查知识库的问题时应调用此工具。
"""

from __future__ import annotations

import logging

from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from common import settings
from common.constants import LLMType

from .base import tool, get_ctx, mcp_json_response

logger = logging.getLogger("ragflow.agent_v2.rag_retrieve")


@tool(
    name="rag_retrieve",
    description=(
        "Use this tool when you need to look up factual content in the "
        "knowledge base to answer the user's question.\n\n"
        "Returns the top_n most similar chunks across all KBs in scope, "
        "each with its source document name, verbatim text, and similarity "
        "score.\n\n"
        "Usage notes:\n"
        "- Use short keyword-style queries (core nouns + verbs). Full-sentence "
        "queries dilute similarity scores.\n"
        "- Call 1-3 times per turn, varying keywords if the first pass "
        "returns sparse results.\n"
        "- Assign [1], [2], [3] to chunks in the order they appear in the "
        "aggregated results across this turn; reuse those markers in your answer."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Keyword-style query; prefer the core nouns and verbs "
                    "from the user's question over a full sentence."
                ),
            },
            "top_n": {
                "type": "integer",
                "description": "Return the top N chunks. Default 8, max 30.",
                "default": 8,
                "minimum": 1,
                "maximum": 30,
            },
            "similarity_threshold": {
                "type": "number",
                "description": (
                    "Drop chunks with hybrid similarity below this score "
                    "(0-1; combines cosine vector sim + BM25 term score). "
                    "Typical useful range 0.10-0.30; values > 0.5 are too "
                    "strict and often return empty. Default 0.15 works for "
                    "most Chinese policy KBs."
                ),
                "default": 0.15,
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["query"],
    },
)
async def rag_retrieve(args: dict) -> dict:
    """MCP tool 入口。

    Args:
        args: ``{"query": str, "top_n": int, "similarity_threshold": float}``

    Returns:
        MCP tool-result 格式 ``{"content": [{"type": "text", "text": "<json>"}]}``
        JSON 负载结构：
            - query: 实际检索的 query
            - total: 召回总片段数
            - chunks: [{doc_name, content, similarity, doc_id, page}, ...]
            - doc_aggs: [{doc_name, doc_id, count}, ...] — 按文档聚合统计
    """
    ctx = get_ctx()
    kb_ids: list[str] = list(ctx.kb_ids)

    query = str(args.get("query", "")).strip()
    if not query:
        return mcp_json_response({"error": "query 不能为空"})
    top_n = int(args.get("top_n", 8))
    similarity_threshold = float(args.get("similarity_threshold", 0.15))

    # 深度防御：即使 session 持久化的 kb_ids 是脏数据，工具执行时也要再校验
    # 一次。能检索的 kb_ids 必须在用户的可访问列表里。
    if ctx.user_id:
        from api.db.services.audit_log_service import AuditLogService
        from api.db.services.dataset_access_service import (
            DatasetAccessService,
            DatasetRole,
        )

        accessible = DatasetAccessService.filter_accessible_kb_ids(
            kb_ids, ctx.user_id, DatasetRole.VIEWER
        )
        denied = [k for k in kb_ids if k not in accessible]
        if denied:
            for k in denied:
                AuditLogService.deny(
                    user_id=ctx.user_id,
                    tenant_id=ctx.tenant_id,
                    action="kb.retrieve",
                    resource_type="knowledgebase",
                    resource_id=k,
                    reason="rag_retrieve_no_access",
                )
            if not accessible:
                return mcp_json_response({
                    "error": "no_access",
                    "message": f"调用方没有访问以下知识库的权限: {denied}",
                })
            # 部分有权限：用可访问的子集继续
            kb_ids = accessible

    # 按 kb_ids 拿 KB 配置（关键：所有 kb 必须用同一个 embedding 模型）
    kbs = KnowledgebaseService.get_by_ids(kb_ids)
    if not kbs:
        return mcp_json_response({"error": f"未找到知识库: {kb_ids}"})

    embedding_list = list(set([kb.embd_id for kb in kbs]))
    if len(embedding_list) > 1:
        return mcp_json_response(
            {
                "error": "所选多个知识库使用了不同的 embedding 模型，"
                "请在 Agent 配置中只选一组共享同一 embedding 的知识库。",
                "embeddings": embedding_list,
            }
        )

    embd_owner_tenant_id = kbs[0].tenant_id
    embd_model_config = get_model_config_by_type_and_name(
        embd_owner_tenant_id, LLMType.EMBEDDING, embedding_list[0]
    )
    embd_mdl = LLMBundle(embd_owner_tenant_id, embd_model_config)

    tenant_ids = list(set([kb.tenant_id for kb in kbs]))

    retriever = settings.retriever
    logger.info(
        "rag_retrieve: query=%r kb_ids=%s top_n=%d thr=%.2f",
        query[:80],
        kb_ids,
        top_n,
        similarity_threshold,
    )

    kbinfos = await retriever.retrieval(
        question=query,
        embd_mdl=embd_mdl,
        tenant_ids=tenant_ids,
        kb_ids=kb_ids,
        page=1,
        page_size=top_n,
        similarity_threshold=similarity_threshold,
        vector_similarity_weight=0.3,
        top=1024,
        aggs=True,
        rerank_mdl=None,
    )

    # 去掉向量字段（避免体积和隐私问题），保留可阅读信息
    chunks_out = []
    for ck in kbinfos.get("chunks", []):
        chunks_out.append(
            {
                "doc_name": ck.get("docnm_kwd", ""),
                "doc_id": ck.get("doc_id", ""),
                "content": ck.get("content_with_weight", ""),
                "similarity": round(ck.get("similarity", 0.0), 4),
                "page": (ck.get("page_num_int") or [None])[0],
                "position": ck.get("position_int"),
            }
        )

    doc_aggs_out = []
    for agg in kbinfos.get("doc_aggs", []):
        doc_aggs_out.append(
            {
                "doc_name": agg.get("doc_name"),
                "doc_id": agg.get("doc_id"),
                "count": agg.get("count", 0),
            }
        )

    result = {
        "query": query,
        "total": kbinfos.get("total", len(chunks_out)),
        "chunks": chunks_out,
        "doc_aggs": doc_aggs_out,
    }
    return mcp_json_response(result)
