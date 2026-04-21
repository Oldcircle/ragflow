"""测试 api/db/services/agent_v2_service.py — Session/Message/ToolCall CRUD。

这些测试会写**真实 MySQL**（复用 `settings.init_settings()` 建立的连接）。
每个测试用自己的 session_id 隔离，测完软删，避免干扰开发数据。
"""

from __future__ import annotations

import os
import pytest

# 在导入 agent_v2_service 前需要先初始化 RAGFlow 的 settings
pytestmark = pytest.mark.skipif(
    os.environ.get("RAGFLOW_TEST_DB") != "1",
    reason="需要真实 MySQL，设 RAGFLOW_TEST_DB=1 启用",
)


@pytest.fixture(scope="module", autouse=True)
def _init_ragflow():
    from common import settings as rf_settings

    rf_settings.init_settings()
    from api.db.db_models import init_database_tables

    init_database_tables()


@pytest.fixture
def svc():
    from api.db.services.agent_v2_service import (
        AgentV2MessageService,
        AgentV2SessionService,
        AgentV2ToolCallService,
    )

    return AgentV2SessionService, AgentV2MessageService, AgentV2ToolCallService


@pytest.fixture
def tenant_kb():
    # 使用开发环境里已有的保障房 KB
    return (
        os.environ.get("AGENT_V2_TEST_TENANT_ID", "968bd6ec3c9f11f1afc91f3c182e7a61"),
        os.environ.get("AGENT_V2_TEST_KB_ID", "a15948b83d5111f1afc91f3c182e7a61"),
    )


def test_session_create_get_update_delete(svc, tenant_kb):
    SessionSvc, MsgSvc, ToolSvc = svc
    tenant_id, kb_id = tenant_kb

    # Create
    s = SessionSvc.create_session(
        tenant_id=tenant_id,
        user_id="pytest-user",
        name="pytest-session",
        kb_ids=[kb_id],
        system_prompt="ignore me",
    )
    assert s.id
    session_id = s.id

    try:
        # Get
        got = SessionSvc.get_by_id(session_id)
        assert got is not None
        assert got.name == "pytest-session"
        assert got.tenant_id == tenant_id
        assert got.kb_ids == [kb_id]

        # List
        listed = SessionSvc.list_by_tenant(tenant_id, user_id="pytest-user")
        ids = [r["id"] for r in listed]
        assert session_id in ids

        # Update (only whitelist fields)
        n = SessionSvc.update_fields(
            session_id,
            name="pytest-renamed",
            max_turns=5,
            evil_field="ignored",  # 应该被过滤
        )
        assert n == 1
        got = SessionSvc.get_by_id(session_id)
        assert got.name == "pytest-renamed"
        assert got.max_turns == 5

        # Append messages
        m1 = MsgSvc.append(session_id=session_id, role="user", content="q1")
        m2 = MsgSvc.append(
            session_id=session_id, role="assistant", content="a1",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        assert m1.id and m2.id

        msgs = MsgSvc.list_by_session(session_id)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["usage"]["input_tokens"] == 10

        # Tool call lifecycle
        tool_use_id = "toolu_test_" + session_id[:8]
        ToolSvc.record_start(
            tool_use_id=tool_use_id,
            session_id=session_id,
            message_id=m2.id,
            tool_name="rag_retrieve",
            args={"query": "testing"},
        )
        n = ToolSvc.record_end(
            tool_use_id=tool_use_id,
            result={"total": 3, "chunks": []},
            duration_ms=123,
        )
        assert n == 1
        tcs = ToolSvc.list_by_session(session_id)
        assert len(tcs) == 1
        assert tcs[0]["status"] == "success"
        assert tcs[0]["duration_ms"] == 123

        # Soft delete
        n = SessionSvc.soft_delete(session_id)
        assert n == 1
        got = SessionSvc.get_by_id(session_id)
        assert got.status == "deleted"
        listed_active = SessionSvc.list_by_tenant(tenant_id, user_id="pytest-user")
        assert session_id not in [r["id"] for r in listed_active]

    finally:
        # Hard cleanup 避免污染数据库
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


def test_session_create_validation(svc, tenant_kb):
    SessionSvc, *_ = svc
    tenant_id, kb_id = tenant_kb

    with pytest.raises(ValueError, match="tenant_id"):
        SessionSvc.create_session(
            tenant_id="", user_id=None, name="x", kb_ids=[kb_id]
        )
    with pytest.raises(ValueError, match="kb_id"):
        SessionSvc.create_session(
            tenant_id=tenant_id, user_id=None, name="x", kb_ids=[]
        )


def test_message_append_role_validation(svc, tenant_kb):
    _, MsgSvc, _ = svc
    with pytest.raises(ValueError, match="role"):
        MsgSvc.append(session_id="dummy", role="invalid-role")
