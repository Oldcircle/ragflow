#!/usr/bin/env python
"""Agent v2 端到端持久化验证。

不走 HTTP（避 RSA 加密登录），但覆盖了 HTTP 端点内部的完整数据流：
  Session Service 创建
  → Runner 跑一轮（ContextVar 注入 + MCP 工具调用）
  → Message/ToolCall Service 落库
  → 查回来验证三表有数据

等同 M1.3 HTTP 层的「功能验收」，只差 Quart 路由转换。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


TENANT_ID = os.environ.get(
    "AGENT_V2_TEST_TENANT_ID", "968bd6ec3c9f11f1afc91f3c182e7a61"
)
KB_ID = os.environ.get("AGENT_V2_TEST_KB_ID", "a15948b83d5111f1afc91f3c182e7a61")
USER_ID = "pytest-e2e"

SYSTEM_PROMPT = """你是深圳保障房政策顾问。
用户问涉及政策的问题时必须调 rag_retrieve 工具查询原文；
禁止用训练知识补数字。"""


async def main():
    # 1. 初始化 RAGFlow
    from common import settings as rf_settings

    rf_settings.init_settings()
    from api.db.db_models import init_database_tables

    init_database_tables()

    from api.agent_v2.runner import AgentRunner, ModelConfig
    from api.db.services.agent_v2_service import (
        AgentV2MessageService,
        AgentV2SessionService,
        AgentV2ToolCallService,
    )

    # 2. 创建 session
    print("=" * 70)
    print("STEP 1: 创建 session")
    print("=" * 70)
    session = AgentV2SessionService.create_session(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        name="pytest-e2e session",
        kb_ids=[KB_ID],
        system_prompt=SYSTEM_PROMPT,
        model_config={
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/anthropic",
        },
        max_turns=6,
        max_budget_usd=0.5,
    )
    print(f"✓ session.id = {session.id}")
    session_id = session.id

    try:
        # 3. 登记 user 消息
        print()
        print("=" * 70)
        print("STEP 2: 登记 user 消息")
        print("=" * 70)
        user_msg = AgentV2MessageService.append(
            session_id=session_id,
            role="user",
            content="公共租赁住房的申请条件有哪些？",
        )
        print(f"✓ user_msg.id = {user_msg.id}")

        # 4. 跑 Runner
        print()
        print("=" * 70)
        print("STEP 3: Runner 跑一轮（会调 rag_retrieve）")
        print("=" * 70)
        model_cfg = ModelConfig(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/anthropic",
            auth_token=os.environ.get(
                "AGENT_V2_DEEPSEEK_KEY",
                "sk-5c8fa7eb9ce84178b0bda88a58055d32",
            ),
        )
        runner = AgentRunner(
            tenant_id=session.tenant_id,
            kb_ids=list(session.kb_ids),
            system_prompt=session.system_prompt,
            model=model_cfg,
            max_turns=session.max_turns,
            max_budget_usd=session.max_budget_usd,
        )

        from common.misc_utils import get_uuid

        assistant_msg_id = get_uuid()
        text_buf: list[str] = []
        tool_ids: list[str] = []
        usage: dict = {}
        tool_starts: dict[str, dict] = {}

        async for ev in runner.run(
            "公共租赁住房的申请条件有哪些？需要社保多少年？"
        ):
            d = ev.to_dict()
            t = d["type"]
            if t == "text_delta":
                text_buf.append(d["data"].get("text", ""))
            elif t == "tool_call_start":
                tool_ids.append(d["data"]["id"])
                tool_starts[d["data"]["id"]] = {
                    "name": d["data"]["name"],
                    "args": d["data"].get("args", {}),
                }
                print(f"  → tool_call_start: {d['data']['name']}")
                AgentV2ToolCallService.record_start(
                    tool_use_id=d["data"]["id"],
                    session_id=session_id,
                    message_id=assistant_msg_id,
                    tool_name=d["data"]["name"],
                    args=d["data"].get("args", {}),
                )
            elif t == "tool_call_end":
                print(
                    f"  ← tool_call_end: {d['data'].get('duration_ms')}ms "
                    f"{'ERROR' if d['data'].get('error') else 'ok'}"
                )
                AgentV2ToolCallService.record_end(
                    tool_use_id=d["data"]["id"],
                    result=d["data"].get("result"),
                    error=d["data"].get("error"),
                    duration_ms=d["data"].get("duration_ms"),
                )
            elif t == "end":
                usage = d["data"].get("usage") or {}

        # 5. 登记 assistant 消息
        print()
        print("=" * 70)
        print("STEP 4: 登记 assistant 消息")
        print("=" * 70)
        final_text = "".join(text_buf)
        AgentV2MessageService.append(
            session_id=session_id,
            role="assistant",
            content=final_text,
            tool_call_ids=tool_ids,
            usage=usage,
            message_id=assistant_msg_id,
        )
        print(f"✓ assistant_msg_id = {assistant_msg_id}")
        print(f"  text length: {len(final_text)} chars")
        print(f"  tool calls: {len(tool_ids)}")
        print(f"  usage: {json.dumps(usage, ensure_ascii=False)[:200]}")

        # 6. 读回验证三表
        print()
        print("=" * 70)
        print("STEP 5: 三表校验")
        print("=" * 70)
        s = AgentV2SessionService.get_by_id(session_id)
        msgs = AgentV2MessageService.list_by_session(session_id)
        tcs = AgentV2ToolCallService.list_by_session(session_id)

        print(f"  agent_v2_session:   {1 if s else 0} row(s)")
        print(f"  agent_v2_message:   {len(msgs)} row(s) (期望 ≥ 2)")
        print(f"  agent_v2_tool_call: {len(tcs)} row(s) (期望 ≥ 1 当 Agent 调过工具)")
        assert s is not None, "session 读回失败"
        assert len(msgs) >= 2, f"message 应 ≥ 2（user + assistant），实际 {len(msgs)}"
        for m in msgs:
            print(f"    - [{m['role']}] {(m['content'] or '')[:60]}...")

        if tcs:
            for c in tcs:
                print(
                    f"    - {c['tool_name']} status={c['status']} "
                    f"duration={c['duration_ms']}ms"
                )

        print()
        print("✅ M1.3 端到端持久化验证通过")

    finally:
        # 清理测试数据
        from api.db.db_models import (
            DB,
            AgentV2Message,
            AgentV2Session,
            AgentV2ToolCall,
        )

        with DB.connection_context():
            AgentV2ToolCall.delete().where(
                AgentV2ToolCall.session_id == session_id
            ).execute()
            AgentV2Message.delete().where(
                AgentV2Message.session_id == session_id
            ).execute()
            AgentV2Session.delete().where(AgentV2Session.id == session_id).execute()
        print()
        print("🧹 测试数据已清理")


if __name__ == "__main__":
    asyncio.run(main())
