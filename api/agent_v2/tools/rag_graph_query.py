"""rag_graph_query 工具 — GraphRAG 实体/关系检索。

RAGFlow 的 GraphRAG 模块（rag/graphrag/）在索引阶段会抽取实体和关系，
形成知识图谱（存在 ES/Infinity 的同一索引里，用 knowledge_graph_kwd 标识）。
本工具让 Agent 直接按实体或问题查询图谱上下文。

注意：只有建知识库时勾选了「Knowledge Graph」的 KB 才有图谱数据；
对没建图谱的 KB 调此工具会返回空结果。
"""

from __future__ import annotations

import logging

from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from common import settings
from common.constants import LLMType

from .base import get_ctx, mcp_json_response, tool

logger = logging.getLogger("ragflow.agent_v2.rag_graph_query")


@tool(
    name="rag_graph_query",
    description=(
        "Use this tool when you need entity-relationship reasoning across "
        "documents — e.g. 'how does X relate to Y', 'which entities belong "
        "to category Z', or chained 'hit entity → related entities' "
        "inferences.\n\n"
        "Only works when the KB was indexed with GraphRAG enabled; otherwise "
        "returns empty results. Prefer `rag_retrieve` for ordinary semantic "
        "questions and only reach here when vanilla retrieval is insufficient."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Entity name or short question (e.g. 'public rental "
                    "housing' or 'housing subsidy formula')."
                ),
            },
            "ent_topn": {
                "type": "integer",
                "default": 6,
                "minimum": 1,
                "maximum": 20,
                "description": "Top N entities to return. Default 6.",
            },
            "rel_topn": {
                "type": "integer",
                "default": 6,
                "minimum": 1,
                "maximum": 20,
                "description": "Top N relations to return. Default 6.",
            },
            "max_token": {
                "type": "integer",
                "default": 4096,
                "minimum": 512,
                "maximum": 8192,
                "description": "Max tokens in the combined graph context.",
            },
        },
        "required": ["query"],
    },
)
async def rag_graph_query(args: dict) -> dict:
    ctx = get_ctx()
    kb_ids = list(ctx.kb_ids)

    query = str(args.get("query", "")).strip()
    if not query:
        return mcp_json_response({"error": "query 不能为空"})
    ent_topn = int(args.get("ent_topn", 6))
    rel_topn = int(args.get("rel_topn", 6))
    max_token = int(args.get("max_token", 4096))

    kbs = KnowledgebaseService.get_by_ids(kb_ids)
    if not kbs:
        return mcp_json_response({"error": f"未找到知识库: {kb_ids}"})

    embedding_list = list(set([kb.embd_id for kb in kbs]))
    if len(embedding_list) > 1:
        return mcp_json_response(
            {
                "error": "所选多个知识库使用了不同的 embedding 模型",
                "embeddings": embedding_list,
            }
        )

    embd_owner_tenant_id = kbs[0].tenant_id
    embd_model_config = get_model_config_by_type_and_name(
        embd_owner_tenant_id, LLMType.EMBEDDING, embedding_list[0]
    )
    embd_mdl = LLMBundle(embd_owner_tenant_id, embd_model_config)

    # GraphRAG 需要一个 chat llm 做 query rewrite 和 summarization
    # 用 KB 所在 tenant 的默认 chat 模型
    try:
        from api.db.joint_services.tenant_model_service import (
            get_tenant_default_model_by_type,
        )

        chat_config = get_tenant_default_model_by_type(
            embd_owner_tenant_id, LLMType.CHAT
        )
        chat_mdl = LLMBundle(embd_owner_tenant_id, chat_config)
    except Exception as exc:  # noqa: BLE001
        return mcp_json_response(
            {"error": f"无法加载 chat 模型用于 GraphRAG：{exc}"}
        )

    tenant_ids = list(set([kb.tenant_id for kb in kbs]))

    kg_retriever = settings.kg_retriever
    logger.info(
        "rag_graph_query: query=%r kb_ids=%s ent=%d rel=%d",
        query[:80],
        kb_ids,
        ent_topn,
        rel_topn,
    )

    try:
        result = await kg_retriever.retrieval(
            question=query,
            tenant_ids=tenant_ids,
            kb_ids=kb_ids,
            emb_mdl=embd_mdl,
            llm=chat_mdl,
            max_token=max_token,
            ent_topn=ent_topn,
            rel_topn=rel_topn,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("kg_retriever.retrieval failed")
        return mcp_json_response(
            {
                "error": "GraphRAG 检索失败——可能是 KB 未启用 Knowledge Graph",
                "detail": str(exc)[:300],
            }
        )

    # result 通常是 {"content_with_weight": "<summarized context>", ...}
    if isinstance(result, dict):
        content = result.get("content_with_weight") or result.get("content") or ""
        return mcp_json_response(
            {
                "query": query,
                "graph_context": content,
                "raw_keys": list(result.keys()),
            }
        )
    return mcp_json_response({"query": query, "graph_context": str(result)})
