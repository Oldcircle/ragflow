"""Phase 2.5.2 — compactor 单测。

覆盖目标：
- ``should_compact`` 阈值边界
- ``split_for_compact`` 划分语义（消息数 ≤/> recent_keep）
- ``summarize_history`` 对 auth_token 空 / 非 200 / 异常 的兜底
- ``maybe_compact_session`` 在不同 session state 下的行为
- ``run_compact_safely`` 异常路径必须写审计且不抛
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.agent_v2 import compactor as compactor_mod
from api.agent_v2.compactor import (
    DEFAULT_COMPACT_TRIGGER_MSGS,
    DEFAULT_RECENT_KEEP_MSGS,
    maybe_compact_session,
    run_compact_safely,
    should_compact,
    split_for_compact,
    summarize_history,
)


# ───────── should_compact ─────────


@pytest.mark.p2
class TestShouldCompact:
    def test_below_threshold(self):
        assert should_compact(total_messages_since_last_compact=5) is False

    def test_at_threshold(self):
        assert should_compact(
            total_messages_since_last_compact=DEFAULT_COMPACT_TRIGGER_MSGS
        ) is True

    def test_above_threshold(self):
        assert should_compact(
            total_messages_since_last_compact=DEFAULT_COMPACT_TRIGGER_MSGS + 1
        ) is True

    def test_custom_threshold(self):
        assert should_compact(total_messages_since_last_compact=5, trigger_msgs=4) is True
        assert should_compact(total_messages_since_last_compact=3, trigger_msgs=4) is False


# ───────── split_for_compact ─────────


@pytest.mark.p2
class TestSplitForCompact:
    def test_fewer_than_keep_returns_empty_summary(self):
        msgs = [{"role": "user", "content": str(i)} for i in range(5)]
        to_sum, to_keep = split_for_compact(msgs, recent_keep=10)
        assert to_sum == []
        assert to_keep == msgs

    def test_exactly_keep_returns_empty_summary(self):
        msgs = [{"role": "user", "content": str(i)} for i in range(10)]
        to_sum, to_keep = split_for_compact(msgs, recent_keep=10)
        assert to_sum == []
        assert len(to_keep) == 10

    def test_more_than_keep_splits_correctly(self):
        msgs = [{"role": "user", "content": str(i)} for i in range(25)]
        to_sum, to_keep = split_for_compact(msgs, recent_keep=10)
        assert len(to_sum) == 15
        assert len(to_keep) == 10
        # 保留的是最近 10 条，所以最后一条 content="24"
        assert to_keep[-1]["content"] == "24"

    def test_empty_input(self):
        to_sum, to_keep = split_for_compact([], recent_keep=DEFAULT_RECENT_KEEP_MSGS)
        assert to_sum == []
        assert to_keep == []


# ───────── summarize_history ─────────


@pytest.mark.p1
@pytest.mark.asyncio
class TestSummarizeHistory:
    @pytest.fixture
    def sample_msgs(self):
        return [
            {"role": "user", "content": "深圳公租房申请条件？"},
            {"role": "assistant", "content": "需年满 18 周岁并社保满 3 年 [1]。"},
        ]

    async def test_empty_auth_token_returns_empty(self, sample_msgs):
        assert await summarize_history(
            sample_msgs, model="x", base_url=None, auth_token=""
        ) == ""

    async def test_empty_messages_returns_empty(self):
        assert await summarize_history(
            [], model="x", base_url=None, auth_token="sk"
        ) == ""

    async def test_successful_summary(self, sample_msgs):
        mock_resp = _FakeResp(200, {
            "content": [{"type": "text", "text": "用户问公租房条件；回答 18 岁 + 3 年社保 [1]。"}]
        })
        with patch("httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await summarize_history(
                sample_msgs, model="x", base_url=None, auth_token="sk"
            )
        assert "公租房" in result

    async def test_non_200_returns_empty(self, sample_msgs):
        mock_resp = _FakeResp(429, {}, text="rate limited")
        with patch("httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await summarize_history(
                sample_msgs, model="x", base_url=None, auth_token="sk"
            )
        assert result == ""

    async def test_exception_returns_empty(self, sample_msgs):
        with patch("httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=RuntimeError("net")
            )
            result = await summarize_history(
                sample_msgs, model="x", base_url=None, auth_token="sk"
            )
        assert result == ""

    async def test_previous_summary_included(self, sample_msgs):
        captured = {}

        class _Client:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def post(self_inner, endpoint, headers=None, json=None):
                captured["payload"] = json
                return _FakeResp(200, {"content": [{"type": "text", "text": "ok"}]})

        with patch("httpx.AsyncClient", return_value=_Client()):
            await summarize_history(
                sample_msgs,
                model="x",
                base_url=None,
                auth_token="sk",
                previous_summary="earlier ctx",
            )
        user_msg = captured["payload"]["messages"][0]["content"]
        assert "earlier-summary" in user_msg
        assert "earlier ctx" in user_msg


# ───────── maybe_compact_session ─────────


@pytest.mark.p1
@pytest.mark.asyncio
class TestMaybeCompactSession:
    async def test_missing_session_returns_false(self):
        with patch.object(
            compactor_mod, "maybe_compact_session", wraps=maybe_compact_session
        ), patch("api.db.services.agent_v2_service.AgentV2SessionService.get_by_id", return_value=None):
            result = await maybe_compact_session(
                session_id="nope",
                model="x",
                base_url=None,
                auth_token="sk",
            )
        assert result is False

    async def test_below_trigger_returns_false(self):
        fake_session = _FakeSession(summary_text="", summary_until_seq=0)
        msgs = [{"id": f"m{i}", "role": "user", "content": f"q{i}"} for i in range(5)]
        with patch(
            "api.db.services.agent_v2_service.AgentV2SessionService.get_by_id",
            return_value=fake_session,
        ), patch(
            "api.db.services.agent_v2_service.AgentV2MessageService.list_by_session",
            return_value=msgs,
        ):
            result = await maybe_compact_session(
                session_id="s1",
                model="x",
                base_url=None,
                auth_token="sk",
                trigger_msgs=20,
            )
        assert result is False

    async def test_summarizer_empty_returns_false_and_no_save(self):
        fake_session = _FakeSession(summary_text="", summary_until_seq=0)
        msgs = [{"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant",
                 "content": f"c{i}"} for i in range(25)]
        save_called = []
        with patch(
            "api.db.services.agent_v2_service.AgentV2SessionService.get_by_id",
            return_value=fake_session,
        ), patch(
            "api.db.services.agent_v2_service.AgentV2MessageService.list_by_session",
            return_value=msgs,
        ), patch(
            "api.agent_v2.compactor.summarize_history",
            AsyncMock(return_value=""),
        ), patch(
            "api.db.services.agent_v2_service.AgentV2SessionService.save_summary",
            side_effect=lambda **kw: save_called.append(kw),
        ):
            result = await maybe_compact_session(
                session_id="s1",
                model="x",
                base_url=None,
                auth_token="sk",
                trigger_msgs=20,
            )
        assert result is False
        assert save_called == []  # 摘要空就不写

    async def test_happy_path_writes_summary(self):
        fake_session = _FakeSession(summary_text="", summary_until_seq=0)
        msgs = [
            {"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"c{i}"}
            for i in range(25)
        ]
        save_args = []
        with patch(
            "api.db.services.agent_v2_service.AgentV2SessionService.get_by_id",
            return_value=fake_session,
        ), patch(
            "api.db.services.agent_v2_service.AgentV2MessageService.list_by_session",
            return_value=msgs,
        ), patch(
            "api.agent_v2.compactor.summarize_history",
            AsyncMock(return_value="SUMMARY_TEXT"),
        ), patch(
            "api.db.services.agent_v2_service.AgentV2SessionService.save_summary",
            side_effect=lambda **kw: save_args.append(kw),
        ):
            result = await maybe_compact_session(
                session_id="s1",
                model="x",
                base_url=None,
                auth_token="sk",
                trigger_msgs=20,
                recent_keep=10,
            )
        assert result is True
        assert len(save_args) == 1
        assert save_args[0]["session_id"] == "s1"
        assert save_args[0]["summary_text"] == "SUMMARY_TEXT"
        # 25 条里压前 15 条 → summary_until_seq 指向第 15 条的 all_msgs 下标（1-based）
        assert save_args[0]["summary_until_seq"] == 15

    async def test_prev_summary_skips_already_compacted_range(self):
        """prev_until=10 时只能对后续消息再压一遍。"""
        fake_session = _FakeSession(summary_text="OLD", summary_until_seq=10)
        msgs = [
            {"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"c{i}"}
            for i in range(35)
        ]
        with patch(
            "api.db.services.agent_v2_service.AgentV2SessionService.get_by_id",
            return_value=fake_session,
        ), patch(
            "api.db.services.agent_v2_service.AgentV2MessageService.list_by_session",
            return_value=msgs,
        ), patch(
            "api.agent_v2.compactor.summarize_history",
            AsyncMock(return_value="NEW_SUMMARY"),
        ), patch(
            "api.db.services.agent_v2_service.AgentV2SessionService.save_summary",
        ) as save_mock:
            result = await maybe_compact_session(
                session_id="s1",
                model="x",
                base_url=None,
                auth_token="sk",
                trigger_msgs=20,
                recent_keep=10,
            )
        assert result is True
        save_mock.assert_called_once()


# ───────── run_compact_safely ─────────


@pytest.mark.p0
@pytest.mark.asyncio
class TestRunCompactSafely:
    async def test_exception_is_caught_and_audited(self):
        audit_calls = []

        def _fake_audit(**kw):
            audit_calls.append(kw)

        with patch(
            "api.agent_v2.compactor.maybe_compact_session",
            AsyncMock(side_effect=RuntimeError("DB down")),
        ), patch(
            "api.db.services.audit_log_service.AuditLogService.log",
            side_effect=_fake_audit,
        ):
            # 必须不抛
            result = await run_compact_safely(
                session_id="s1",
                tenant_id="t1",
                user_id="u1",
                model="x",
                base_url=None,
                auth_token="sk",
            )
        assert result is False
        assert len(audit_calls) == 1
        rec = audit_calls[0]
        assert rec["action"] == "agent_v2.compact"
        assert rec["result"] == "deny"
        assert "RuntimeError" in rec["reason"]
        assert rec["resource_id"] == "s1"

    async def test_success_writes_allow_audit(self):
        audit_calls = []

        def _fake_audit(**kw):
            audit_calls.append(kw)

        with patch(
            "api.agent_v2.compactor.maybe_compact_session",
            AsyncMock(return_value=True),
        ), patch(
            "api.db.services.audit_log_service.AuditLogService.log",
            side_effect=_fake_audit,
        ):
            result = await run_compact_safely(
                session_id="s1",
                tenant_id="t1",
                user_id="u1",
                model="x",
                base_url=None,
                auth_token="sk",
            )
        assert result is True
        assert audit_calls[0]["result"] == "allow"
        assert audit_calls[0]["reason"] == "summary_written"

    async def test_skip_also_audited_as_allow(self):
        audit_calls = []

        def _fake_audit(**kw):
            audit_calls.append(kw)

        with patch(
            "api.agent_v2.compactor.maybe_compact_session",
            AsyncMock(return_value=False),
        ), patch(
            "api.db.services.audit_log_service.AuditLogService.log",
            side_effect=_fake_audit,
        ):
            result = await run_compact_safely(
                session_id="s1",
                tenant_id="t1",
                user_id="u1",
                model="x",
                base_url=None,
                auth_token="sk",
            )
        assert result is False
        assert audit_calls[0]["result"] == "allow"
        assert audit_calls[0]["reason"] == "skipped"

    async def test_audit_failure_does_not_break(self):
        """审计写入本身挂了，run_compact_safely 仍该干净返回。"""
        with patch(
            "api.agent_v2.compactor.maybe_compact_session",
            AsyncMock(return_value=False),
        ), patch(
            "api.db.services.audit_log_service.AuditLogService.log",
            side_effect=RuntimeError("audit DB down"),
        ):
            # 必须不抛
            result = await run_compact_safely(
                session_id="s1",
                tenant_id="t1",
                user_id="u1",
                model="x",
                base_url=None,
                auth_token="sk",
            )
        assert result is False


# ───────── helpers ─────────


class _FakeSession:
    def __init__(self, summary_text="", summary_until_seq=0):
        self.summary_text = summary_text
        self.summary_until_seq = summary_until_seq


class _FakeResp:
    def __init__(self, status_code, body, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body
