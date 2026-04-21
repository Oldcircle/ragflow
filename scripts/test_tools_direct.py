#!/usr/bin/env python
"""直接调用每个工具做 smoke test（绕过 Agent）。"""

from __future__ import annotations

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TENANT_ID = "968bd6ec3c9f11f1afc91f3c182e7a61"
KB_ID = "a15948b83d5111f1afc91f3c182e7a61"


async def main():
    # 初始化 RAGFlow
    from common import settings as rf_settings

    rf_settings.init_settings()

    from api.db.db_models import init_database_tables as init_web_db

    init_web_db()

    from api.agent_v2.tools.base import ToolContext, reset_ctx, set_ctx
    from api.agent_v2.tools.rag_list_docs import rag_list_docs
    from api.agent_v2.tools.rag_read_doc import rag_read_doc
    from api.agent_v2.tools.rag_retrieve import rag_retrieve

    ctx = ToolContext(tenant_id=TENANT_ID, kb_ids=(KB_ID,))
    token = set_ctx(ctx)
    try:
        print("=" * 60)
        print("1. rag_list_docs")
        print("=" * 60)
        r = await rag_list_docs.handler({"page_size": 100})
        text = r["content"][0]["text"]
        payload = json.loads(text)
        print(f"   total_matched: {payload['total_matched']}")
        print(f"   returned: {payload['returned']}")
        if payload["documents"]:
            first = payload["documents"][0]
            print(f"   first doc: {first['name']}")
            first_doc_id = first["doc_id"]

            print()
            print("=" * 60)
            print(f"2. rag_read_doc (doc_id={first_doc_id})")
            print("=" * 60)
            r2 = await rag_read_doc.handler({"doc_id": first_doc_id, "chunk_limit": 3})
            p2 = json.loads(r2["content"][0]["text"])
            print(f"   doc_name: {p2.get('doc_name')}")
            print(f"   total_chunks: {p2.get('total_chunks')}")
            print(f"   returned_chunks: {p2.get('returned_chunks')}")
            if p2.get("chunks"):
                c0 = p2["chunks"][0]
                preview = c0["content"][:100].replace("\n", " ")
                print(f"   first chunk: [order={c0['order']}] {preview}...")

        print()
        print("=" * 60)
        print("3. rag_retrieve")
        print("=" * 60)
        r3 = await rag_retrieve.handler({"query": "公共租赁住房申请条件", "top_n": 3})
        p3 = json.loads(r3["content"][0]["text"])
        print(f"   total: {p3['total']}")
        print(f"   chunks returned: {len(p3['chunks'])}")
        if p3["chunks"]:
            c = p3["chunks"][0]
            print(f"   top match: sim={c['similarity']} from {c['doc_name']}")

        print()
        print("=" * 60)
        print("4. rag_graph_query (可能失败：KB 未启用 Knowledge Graph)")
        print("=" * 60)
        from api.agent_v2.tools.rag_graph_query import rag_graph_query

        r4 = await rag_graph_query.handler({"query": "保障性住房", "ent_topn": 3, "rel_topn": 3})
        p4 = json.loads(r4["content"][0]["text"])
        if "error" in p4:
            print(f"   (expected) {p4['error']}")
        else:
            print(f"   graph_context len: {len(p4.get('graph_context', ''))}")

        print()
        print("✅ 全部直接调用完成")
    finally:
        reset_ctx(token)


if __name__ == "__main__":
    asyncio.run(main())
