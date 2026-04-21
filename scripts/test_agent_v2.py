#!/usr/bin/env python
"""M1.1 Agent v2 命令行验证脚本。

用法::

    # 在 RAGFlow 项目根目录下：
    export PYTHONPATH=$(pwd)
    export NLTK_DATA=./nltk_data
    # 方式 A：用 Anthropic 原生 Claude（需 Anthropic API Key）
    export ANTHROPIC_API_KEY=sk-ant-...
    .venv/bin/python scripts/test_agent_v2.py

    # 方式 B：用 DeepSeek（Anthropic 兼容端点）
    export AGENT_V2_PROVIDER=deepseek
    export DEEPSEEK_API_KEY=sk-...
    .venv/bin/python scripts/test_agent_v2.py

脚本会向保障房知识库 Agent 发一条问题，
观察 Agent 是否会自主调用 rag_retrieve 工具并综合答复。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# 确保能 import api/* 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# RAGFlow 默认租户和保障房知识库的 ID（从 MySQL 里查到的）
DEFAULT_TENANT_ID = "968bd6ec3c9f11f1afc91f3c182e7a61"
DEFAULT_KB_ID = "a15948b83d5111f1afc91f3c182e7a61"

DEFAULT_QUESTION = "深圳公共租赁住房的申请条件有哪些？需要社保多少年？"

SYSTEM_PROMPT = """你是深圳保障房政策顾问，严格基于知识库内容答复。

工作方式：
1. 判断问题是否涉及深圳保障房政策。无关问题：直接说"我只回答深圳保障房政策"，不调工具。
2. 相关问题：必须调用 rag_retrieve 工具检索原文。可根据需要多次检索，换关键词。
3. 严格基于检索结果答复：
   - 所有数字、年限、比例、面积必须有原文支撑；
   - 原文没写的内容，回答"未查到相关规定，建议向深圳住建部门咨询"；
   - 不要把其他城市政策套用到深圳。
4. 回答结尾列出「依据文件」清单。

禁止：
- 编造数字（尤其是年龄、学历、社保年限、收入限额）；
- 用训练知识补充原文未说的内容。
"""


def build_model_config(provider: str):
    """根据 provider 构造 ModelConfig。"""
    from api.agent_v2.runner import ModelConfig

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            sys.exit(
                "ANTHROPIC_API_KEY 未设置。请 export ANTHROPIC_API_KEY=sk-ant-... "
                "或改用 AGENT_V2_PROVIDER=deepseek。"
            )
        return ModelConfig(
            model=os.environ.get("AGENT_V2_MODEL", "claude-sonnet-4-5"),
            auth_token=key,
        )
    if provider == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not key:
            sys.exit(
                "DEEPSEEK_API_KEY 未设置。请 export DEEPSEEK_API_KEY=sk-... 或提供 ANTHROPIC_AUTH_TOKEN。"
            )
        return ModelConfig(
            model=os.environ.get("AGENT_V2_MODEL", "deepseek-chat"),
            base_url=os.environ.get(
                "AGENT_V2_BASE_URL", "https://api.deepseek.com/anthropic"
            ),
            auth_token=key,
        )
    sys.exit(f"未知 provider: {provider}（支持 anthropic / deepseek）")


async def main_async(args):
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # 初始化 RAGFlow settings（否则 `settings.retriever` 不可用）
    from common import settings as rf_settings

    rf_settings.init_settings()

    from api.db.db_models import init_database_tables as init_web_db

    init_web_db()

    from api.agent_v2.runner import AgentRunner

    model_cfg = build_model_config(args.provider)

    runner = AgentRunner(
        tenant_id=args.tenant_id,
        kb_ids=args.kb_ids.split(","),
        system_prompt=SYSTEM_PROMPT,
        model=model_cfg,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
    )

    print("=" * 70)
    print(f"Question: {args.question}")
    print(f"Provider: {args.provider}  Model: {model_cfg.model}")
    print(f"Tenant: {args.tenant_id}  KB: {args.kb_ids}")
    print("=" * 70)

    n_tool_calls = 0
    n_text_chunks = 0

    async for event in runner.run(args.question):
        d = event.to_dict()
        t = d["type"]
        payload = d["data"]
        if t == "text_delta":
            sys.stdout.write(payload["text"])
            sys.stdout.flush()
            n_text_chunks += 1
        elif t == "thinking":
            print(f"\n[THINKING] {payload['text'][:200]}...")
        elif t == "tool_call_start":
            n_tool_calls += 1
            print(
                f"\n\n🔧 [TOOL CALL #{n_tool_calls}] {payload['name']}\n"
                f"    args: {json.dumps(payload['args'], ensure_ascii=False)[:200]}"
            )
        elif t == "tool_call_end":
            result_preview = str(payload.get("result") or payload.get("error") or "")
            if len(result_preview) > 300:
                result_preview = result_preview[:300] + "..."
            print(
                f"✅ [TOOL RESULT] "
                f"({payload['duration_ms']}ms) "
                f"{'ERROR' if payload.get('error') else 'ok'} "
                f"{result_preview}"
            )
        elif t == "error":
            print(f"\n\n❌ [ERROR] {payload['code']}: {payload['message']}")
        elif t == "end":
            print(f"\n\n{'=' * 70}")
            print(f"Usage: {json.dumps(payload.get('usage', {}), ensure_ascii=False)}")
            print(
                f"Tool calls: {n_tool_calls}   "
                f"Text chunks: {n_text_chunks}"
            )
            print("=" * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider",
        default=os.environ.get("AGENT_V2_PROVIDER", "anthropic"),
        choices=["anthropic", "deepseek"],
    )
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--kb-ids", default=DEFAULT_KB_ID)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-budget-usd", type=float, default=0.5)
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
