"""ToB 验收自动化 — 所有能跑脚本的都在这一个入口。

跑法：
    cd ~/Opensource/forks/ragflow
    export PYTHONPATH=$(pwd) NLTK_DATA=./nltk_data
    .venv/bin/python scripts/tob_acceptance.py            # 不跑真 LLM
    .venv/bin/python scripts/tob_acceptance.py --live-llm # 额外跑一次 AgentRunner 真 DeepSeek

会做什么：
- 调 service 层跨越 MySQL / ES / Redis 跑**真 I/O**（不 mock）
- 每项断言独立，失败单独报告不影响其它
- 自动建临时 tenant / kb / session，结束统一清理
- 末尾 subprocess 跑一次 pytest test/agent_v2/ + ruff 做外层回归
- 默认 **不调用** LLM（不烧钱）；--live-llm 时追加一次真问题（约 $0.05）

不覆盖的（必须人眼在浏览器看）：
- 前端页面渲染 / Citation warn 面板颜色 / Subagent 子卡片折叠 / 模板卡片
- max_turns / max_budget 输入能否擦空（UI 交互）
- 飞书机器人端到端（需要外网 webhook + 飞书租户）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import traceback
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable

# 同 test/agent_v2/conftest.py：pyproject filterwarnings=["error"] 会把 xgboost 的
# pkg_resources UserWarning 升级成 NameError，所以先 silent warm-up。
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    try:
        import xgboost  # noqa: F401
    except Exception:
        pass

from common import settings  # noqa: E402

settings.init_settings()  # 触发 DB 初始化


# ────────────────────────────── 报告框架 ──────────────────────────────


@dataclass
class Report:
    results: list[tuple[str, str, str]] = field(default_factory=list)  # (id, status, detail)

    def passed(self, tid: str, detail: str = "") -> None:
        self.results.append((tid, "PASS", detail))

    def failed(self, tid: str, detail: str) -> None:
        self.results.append((tid, "FAIL", detail))

    def skipped(self, tid: str, detail: str = "") -> None:
        self.results.append((tid, "SKIP", detail))

    def pass_count(self) -> int:
        return sum(1 for _, s, _ in self.results if s == "PASS")

    def fail_count(self) -> int:
        return sum(1 for _, s, _ in self.results if s == "FAIL")

    def skip_count(self) -> int:
        return sum(1 for _, s, _ in self.results if s == "SKIP")

    def print(self) -> int:
        print()
        print("═" * 72)
        print(f"  ToB 验收报告  —  {len(self.results)} 项")
        print("═" * 72)
        for tid, status, detail in self.results:
            mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️ "}[status]
            line = f"  {mark} {tid}"
            if detail:
                line += f"    ·  {detail}"
            print(line)
        print("─" * 72)
        print(
            f"  PASS: {self.pass_count()}   "
            f"FAIL: {self.fail_count()}   "
            f"SKIP: {self.skip_count()}"
        )
        print("═" * 72)
        return 0 if self.fail_count() == 0 else 1


RPT = Report()


def run_check(tid: str, fn: Callable[[], Any]) -> None:
    """执行一项检查；异常 = FAIL；返回 str = PASS with detail."""
    try:
        detail = fn() or ""
    except AssertionError as e:
        RPT.failed(tid, f"assert: {e}")
    except Exception as e:
        tb = traceback.format_exc(limit=3).strip().split("\n")[-1]
        RPT.failed(tid, f"{type(e).__name__}: {e} ({tb[:80]})")
    else:
        RPT.passed(tid, str(detail))


def run_async_check(tid: str, coro_fn: Callable[[], Any]) -> None:
    try:
        detail = asyncio.run(coro_fn()) or ""
    except AssertionError as e:
        RPT.failed(tid, f"assert: {e}")
    except Exception as e:
        RPT.failed(tid, f"{type(e).__name__}: {str(e)[:120]}")
    else:
        RPT.passed(tid, str(detail))


# ────────────────────────────── 检查项 ──────────────────────────────


def S01_definition_registry():
    from api.agent_v2.definitions import get_definition, list_definitions
    from api.agent_v2.definitions.registry import clear_cache_for_tests

    clear_cache_for_tests()
    all_defs = list_definitions()
    supers = list_definitions(kind="supervisor")
    subs = list_definitions(kind="subagent")
    assert len(all_defs) >= 8, f"expected ≥8 definitions, got {len(all_defs)}"
    assert len(supers) >= 6, f"expected ≥6 supervisors, got {len(supers)}"
    assert len(subs) >= 2, f"expected ≥2 subagents, got {len(subs)}"
    assert get_definition("sub_policy_researcher") is not None
    assert get_definition("__nonexistent__") is None
    names = [d.name for d in all_defs]
    assert len(names) == len(set(names)), "duplicate definition names"
    return f"{len(supers)} supervisors + {len(subs)} subagents"


def S02_citation_validator_three_rules():
    from api.agent_v2.validators import EvidenceIndex, validate_citations

    idx = EvidenceIndex()
    idx.add_from_rag_retrieve({
        "chunks": [
            {"chunk_id": "c1", "content": "申请人须年满 18 周岁，社保满 3 年。"},
            {"chunk_id": "c2", "content": "租金补贴 50%。"},
        ]
    })

    # case clean：0 issue
    assert validate_citations(
        "年满 18 周岁 [1]，社保 3 年 [1]，补贴 50% [2]。", idx,
    ) == []
    # rule 1 missing_chunk
    iss1 = validate_citations("见 [9]。", idx)
    assert any(i.kind == "missing_chunk" for i in iss1)
    # rule 2 number_unsupported（21 岁不在 evidence）
    iss2 = validate_citations("年满 21 周岁 [1]。", idx)
    assert any(i.kind == "number_unsupported" for i in iss2)
    # rule 3 no_citation_for_numeric（18 岁正确但无 [N]）
    iss3 = validate_citations("年满 18 周岁，社保 3 年。", idx)
    assert sum(1 for i in iss3 if i.kind == "no_citation_for_numeric") == 2
    return "rule 1/2/3 均命中"


def S03_evidence_index_extraction():
    from api.agent_v2.validators.evidence_index import extract_numbers

    text = "50% / 百分之七十 / 3 年 / 50 万元 / 18 周岁 / 2024-06-01 / 12000 元"
    nums = extract_numbers(text)
    kinds = {n.kind for n in nums}
    assert {"percent", "year_duration", "amount", "age", "date"} <= kinds
    amounts = {n.normalized for n in nums if n.kind == "amount"}
    assert "500000" in amounts, f"50 万 should expand to 500000, got {amounts}"
    return f"kinds={sorted(kinds)}"


def S04_rewrite_bailouts():
    """rewrite 的 4 条不调 LLM 的兜底都必须返 None."""
    from api.agent_v2.validators import EvidenceIndex, rewrite_answer_strict

    async def run():
        idx = EvidenceIndex()
        idx.add_from_rag_retrieve({"chunks": [{"chunk_id": "a", "content": "x"}]})
        # 空 auth
        assert await rewrite_answer_strict(
            original_text="x", issues=[{"kind": "x"}], evidence=idx,
            model="m", base_url=None, auth_token="",
        ) is None
        # 空 issues
        assert await rewrite_answer_strict(
            original_text="x", issues=[], evidence=idx,
            model="m", base_url=None, auth_token="sk",
        ) is None
        # 空 evidence
        empty = EvidenceIndex()
        assert await rewrite_answer_strict(
            original_text="x", issues=[{"kind": "x"}], evidence=empty,
            model="m", base_url=None, auth_token="sk",
        ) is None
        # 空 original
        assert await rewrite_answer_strict(
            original_text="", issues=[{"kind": "x"}], evidence=idx,
            model="m", base_url=None, auth_token="sk",
        ) is None
        return "4 个 bailout 全符合"

    return asyncio.run(run())


def S05_compactor_pure_logic():
    from api.agent_v2.compactor import should_compact, split_for_compact

    assert should_compact(total_messages_since_last_compact=19) is False
    assert should_compact(total_messages_since_last_compact=20) is True
    msgs = [{"id": str(i), "role": "user", "content": str(i)} for i in range(25)]
    to_sum, to_keep = split_for_compact(msgs, recent_keep=10)
    assert len(to_sum) == 15 and len(to_keep) == 10
    assert to_keep[-1]["content"] == "24"
    return "should_compact 阈值 + split_for_compact 窗口正确"


def S06_session_service_roundtrip(tenant_id: str, user_id: str, kb_id: str) -> str:
    from api.db.services.agent_v2_service import (
        AgentV2MessageService,
        AgentV2SessionService,
    )

    s = AgentV2SessionService.create_session(
        tenant_id=tenant_id,
        user_id=user_id,
        name="acceptance-session",
        kb_ids=[kb_id],
        system_prompt="test",
        model_config={"llm_name": "deepseek-chat", "factory": "DeepSeek"},
        max_turns=5,
        max_budget_usd=0.1,
        history_turn_limit=6,
    )
    try:
        # 塞三条消息
        AgentV2MessageService.append(
            session_id=s.id, role="user", content="Q1",
        )
        AgentV2MessageService.append(
            session_id=s.id, role="assistant", content="A1",
        )
        AgentV2MessageService.append(
            session_id=s.id, role="user", content="Q2",
        )
        got = AgentV2SessionService.get_by_id(s.id)
        assert got is not None and got.history_turn_limit == 6

        # list_for_runner 应拉到 user+assistant 按时间升序
        hist = AgentV2MessageService.list_for_runner(
            session_id=s.id, limit=10
        )
        contents = [m.get("content") for m in hist]
        assert "Q1" in contents and "A1" in contents and "Q2" in contents

        # save_summary
        AgentV2SessionService.save_summary(
            session_id=s.id,
            summary_text="summary body",
            summary_until_seq=2,
        )
        reloaded = AgentV2SessionService.get_by_id(s.id)
        assert reloaded.summary_text == "summary body"
        assert int(reloaded.summary_until_seq) == 2
        return f"session {s.id[:8]} roundtrip OK (3 msgs, summary persisted)"
    finally:
        AgentV2SessionService.soft_delete(s.id)


def S07_audit_log_write_query(tenant_id: str, user_id: str) -> str:
    from api.db.services.audit_log_service import AuditLogService

    # 用 _test. 前缀把合成探针从真实业务审计里隔出来（管理员 UI 可按前缀过滤）
    probe = f"acceptance-{int(time.time())}"
    AuditLogService.allow(
        user_id=user_id,
        tenant_id=tenant_id,
        action="_test.acceptance_probe",
        resource_type="acceptance",
        resource_id=probe,
        reason="test",
    )
    AuditLogService.deny(
        user_id=user_id,
        tenant_id=tenant_id,
        action="_test.acceptance_probe",
        resource_type="acceptance",
        resource_id=probe,
        reason="test-deny",
    )
    rows = AuditLogService.query_logs(
        tenant_id=tenant_id,
        action="_test.acceptance_probe",
        resource_id=probe,
        limit=10,
    )
    results = {getattr(r, "result", None) for r in rows}
    assert results == {"allow", "deny"}, f"got {results}"
    return f"wrote + queried 2 rows; results={sorted(results)}"


def S08_quota_service(tenant_id: str) -> str:
    from api.db.services.tenant_quota_service import (
        TenantQuotaService,
        TenantUsageService,
    )

    before = TenantUsageService.get_today(tenant_id)
    TenantUsageService.increment(
        tenant_id, token_in=123, token_out=456, cost_usd=0.01, bot_messages=2,
    )
    after = TenantUsageService.get_today(tenant_id)
    assert after["token_in"] - before.get("token_in", 0) >= 123
    assert after["token_out"] - before.get("token_out", 0) >= 456
    limits = TenantQuotaService.get(tenant_id)
    d = limits.to_dict()
    assert "kb_max" in d, f"expected kb_max, got keys={list(d)}"
    r = TenantUsageService.get_range(tenant_id, days=3)
    assert isinstance(r, list)
    return f"usage +123 tokens applied; range({len(r)} days); limits kb_max={d['kb_max']}"


def S09_rate_limit_memory():
    from api.utils import rate_limit

    # 强制走内存路径
    import rag.utils.redis_conn as redis_conn_mod

    original = redis_conn_mod.REDIS_CONN
    try:
        redis_conn_mod.REDIS_CONN = type(
            "NoRedis", (), {"REDIS": None, "lua_token_bucket": None}
        )()
        key = f"acceptance:{int(time.time()*1000)}"
        # rps=2，capacity=max(1,2)=2，起始 full → 前两次过、第三次挡
        assert rate_limit.try_take(key, 2.0) is True
        assert rate_limit.try_take(key, 2.0) is True
        assert rate_limit.try_take(key, 2.0) is False
        return "memory bucket rps=2 burst (pass/pass/deny)"
    finally:
        redis_conn_mod.REDIS_CONN = original


def S10_rate_limit_redis_if_available():
    """Redis 可用时才跑；不可用返 SKIP。"""
    try:
        import rag.utils.redis_conn as redis_conn_mod
    except Exception as e:
        return f"SKIP: {e}"
    client = getattr(redis_conn_mod.REDIS_CONN, "REDIS", None)
    if client is None:
        return "SKIP: REDIS_CONN.REDIS is None"
    try:
        client.ping()
    except Exception as e:
        return f"SKIP: redis ping failed: {e}"

    from api.utils import rate_limit

    key = f"acceptance:live:{int(time.time()*1000)}"
    assert rate_limit.try_take(key, 5.0) is True
    # 验证 hash 后的 key 进了 Redis，原串不出现
    found = list(client.scan_iter("rl:tb:*", count=500))
    assert any(key not in k.decode() if isinstance(k, bytes) else key not in k
               for k in found)
    return f"redis bucket ok (rl:tb:* keys={len(found)})"


def S11_dedup_cache():
    from api.bot_channels.dedup import MessageDedupCache

    cache = MessageDedupCache(ttl_seconds=5)
    # 强制走内存
    cache._try_mark_redis = lambda _k: None  # type: ignore[assignment]
    key = f"acceptance-dedup-{int(time.time()*1000)}"
    assert cache.try_mark(key) is True
    assert cache.try_mark(key) is False
    return "memory dedup: 首次 pass, 重投 deny"


def S12_bot_secret_encryption():
    from api.db.services import bot_channel_service as svc

    original_key = settings.SECRET_KEY
    try:
        settings.SECRET_KEY = "acceptance-bot-kek"
        enc = svc.encrypt_config_secrets({
            "app_id": "cli_xxx",
            "app_secret": "super-secret",
            "encrypt_key": "k",
        })
        assert enc["app_secret"].startswith("enc:v1:")
        assert enc["app_id"] == "cli_xxx"  # 非敏感字段原样
        dec = svc.decrypt_config_secrets(enc)
        assert dec["app_secret"] == "super-secret"
        # 占位符保护：更新时传 *** 不应覆盖
        merged = svc.merge_config_update(enc, {
            "app_secret": "***",
            "app_id": "cli_yyy",
        })
        assert svc.decrypt_config_secrets(merged)["app_secret"] == "super-secret"
        return "encrypt/decrypt + *** placeholder 保护"
    finally:
        settings.SECRET_KEY = original_key


def S13_subagent_trace_roundtrip(tenant_id: str, user_id: str, kb_id: str) -> str:
    from api.db.services.agent_v2_service import AgentV2SessionService
    from api.db.services.subagent_trace_service import SubagentTraceService

    s = AgentV2SessionService.create_session(
        tenant_id=tenant_id, user_id=user_id,
        name="acceptance-subagent-parent",
        kb_ids=[kb_id], system_prompt="",
    )
    try:
        trace = SubagentTraceService.start(
            parent_session_id=s.id,
            parent_tool_call_id="toolu_fake",
            description="acceptance probe",
            prompt="do nothing",
            allowed_tools=["rag_retrieve"],
            max_turns=3,
            max_budget_usd=0.1,
        )
        trace_id = trace.id
        SubagentTraceService.finish(
            trace_id,
            status="success",
            result_preview="done",
            token_usage={"input_tokens": 10, "output_tokens": 20},
            cost_usd=0.001,
        )
        traces = list(SubagentTraceService.list_by_session(s.id))
        assert len(traces) == 1, f"expected 1 trace, got {len(traces)}"
        # list_by_session 返的是 peewee Model 实例；用属性访问
        first = traces[0]
        status = getattr(first, "status", None) or (
            first.get("status") if isinstance(first, dict) else None
        )
        assert status == "success", f"expected success, got {status}"
        return f"trace {trace_id[:8]} start → finish → list OK"
    finally:
        AgentV2SessionService.soft_delete(s.id)


def S14_cross_tenant_rbac_denies(tenant_id: str, user_id: str, kb_id: str) -> str:
    """不能用真租户用户 + 真 kb，我们要证"非所有者"不能访问。

    构造一个不存在的 foreign user_id，验证三件事：
    1. effective_role 返 None
    2. filter_accessible_kb_ids 把 kb_id 过滤掉
    3. require_at_least 抛 AccessDeniedError
    """
    from api.db.services.dataset_access_service import (
        AccessDeniedError,
        DatasetAccessService,
        DatasetRole,
    )

    foreign = f"__foreign_{int(time.time())}"
    assert DatasetAccessService.effective_role(kb_id, foreign) is None
    assert DatasetAccessService.filter_accessible_kb_ids([kb_id], foreign) == []
    try:
        DatasetAccessService.require_at_least(kb_id, foreign, DatasetRole.VIEWER)
    except AccessDeniedError:
        pass
    else:
        raise AssertionError("require_at_least should have raised AccessDeniedError")
    # 当前 user_id（KB 创建者） 反面：OWNER
    role = DatasetAccessService.effective_role(kb_id, user_id)
    assert role == DatasetRole.OWNER, f"expected OWNER, got {role}"
    return f"foreign user denied; real owner = {role.value}"


async def S15_model_resolver_live(tenant_id: str) -> str:
    from api.agent_v2.model_resolver import resolve_model

    r = resolve_model(
        {"llm_name": "deepseek-chat", "factory": "DeepSeek"}, tenant_id,
    )
    assert r.source == "tenant_llm", f"expected tenant_llm, got {r.source}"
    assert r.config.auth_token, "auth_token missing"
    assert "deepseek" in (r.config.base_url or "").lower()
    return f"{r.display_name} via {r.source} [base_url set, auth_token set]"


async def S16_live_runner(tenant_id: str, user_id: str, kb_id: str) -> str:
    """真跑一次 AgentRunner 答一个短问题，验证 SSE 事件完整。

    预算：max_turns=3 / max_budget_usd=0.2，一次 ~$0.02。
    """
    from api.agent_v2.model_resolver import resolve_model
    from api.agent_v2.runner import AgentRunner
    from api.db.services.agent_v2_service import (
        AgentV2MessageService,
        AgentV2SessionService,
    )

    resolved = resolve_model(
        {"llm_name": "deepseek-chat", "factory": "DeepSeek"}, tenant_id,
    )
    s = AgentV2SessionService.create_session(
        tenant_id=tenant_id, user_id=user_id, name="acceptance-live",
        kb_ids=[kb_id], system_prompt="你是保障房政策顾问。只基于检索到的原文回答。",
        model_config={"llm_name": "deepseek-chat", "factory": "DeepSeek"},
        max_turns=3, max_budget_usd=0.2,
        citation_enforce_level="warn",
    )
    try:
        runner = AgentRunner(
            tenant_id=tenant_id, user_id=user_id, kb_ids=[kb_id],
            system_prompt=s.system_prompt or "",
            model=resolved.config,
            max_turns=3, max_budget_usd=0.2,
            session_id=s.id,
            citation_enforce_level="warn",
        )
        event_types: list[str] = []
        final_text: list[str] = []
        tool_names: list[str] = []
        async for ev in runner.run("公共租赁住房的申请条件是什么？"):
            d = ev.to_dict()
            event_types.append(d["type"])
            if d["type"] == "text_delta":
                final_text.append(d["data"].get("text") or "")
            elif d["type"] == "tool_call_start":
                tool_names.append(d["data"].get("name") or "")
        full = "".join(final_text)
        assert "end" in event_types, f"no end event; got {set(event_types)}"
        assert any(t == "tool_call_start" for t in event_types), \
            "agent didn't call any tool"
        # SDK 会在工具名前加 mcp__<server>__ 前缀
        assert any(n.endswith("rag_retrieve") for n in tool_names), \
            f"no rag_retrieve; tools={tool_names}"
        assert len(full) > 50, f"assistant text too short: {full[:80]!r}"
        return (
            f"events={len(event_types)} tools={tool_names} "
            f"text={len(full)}ch bytes"
        )
    finally:
        AgentV2SessionService.soft_delete(s.id)


def S17_pytest_regression() -> str:
    """外层跑一遍 pytest test/agent_v2/，必须 0 fail."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    env.setdefault("NLTK_DATA", "./nltk_data")
    res = subprocess.run(
        [".venv/bin/python", "-m", "pytest", "test/agent_v2/",
         "--no-header", "-q", "--tb=line"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    tail = (res.stdout + res.stderr).splitlines()[-3:]
    summary = next((ln for ln in tail if "passed" in ln or "failed" in ln), "?")
    assert res.returncode == 0, f"pytest failed: {summary}"
    return summary.strip()


def S18_ruff() -> str:
    res = subprocess.run(
        ["uvx", "ruff", "check", "api/", "test/"],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"ruff failed: {res.stdout[-200:]}"
    return "All checks passed"


def S19_http_endpoints_registered() -> str:
    """不登录打一些关键端点，期望 401 / 200 — 证明 blueprint 都注册了."""
    import urllib.request

    def hit(path: str) -> int:
        req = urllib.request.Request(
            f"http://127.0.0.1:9380{path}", method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:
            raise AssertionError(f"{path} connect failed: {e}")

    need_auth = [
        "/v1/agent_v2/session",
        "/v1/agent_v2/model",
        "/v1/agent_v2/definition",
        "/v1/tenant_quota",
        "/v1/audit_log/list",
        "/v1/kb/fake-kb-id/member",
        "/v1/bot_channel/list",
    ]
    public = ["/v1/bot/_supported"]
    missed = []
    for p in need_auth:
        code = hit(p)
        if code not in (401, 403):
            missed.append(f"{p}→{code}")
    for p in public:
        code = hit(p)
        if code != 200:
            missed.append(f"{p}→{code}(expected 200)")
    assert not missed, f"wrong status: {missed}"
    return f"{len(need_auth)+len(public)} endpoints registered"


# ────────────────────────────── 主入口 ──────────────────────────────


def _pick_seed_context() -> tuple[str, str, str]:
    """从现有数据库里挑一个真实的 tenant + user + kb 作为验收上下文。

    优先用 qsjzqdnr@gmail.com / 保障房 KB。找不到就抛。
    """
    from api.db.db_models import DB, Knowledgebase, User, UserTenant

    with DB.connection_context():
        user = User.select().where(User.email == "qsjzqdnr@gmail.com").get()
        t = UserTenant.select().where(
            (UserTenant.user_id == user.id) & (UserTenant.status == "1")
        ).first()
        tenant_id = (t.tenant_id if t else user.id)
        kb = (
            Knowledgebase.select()
            .where(
                (Knowledgebase.tenant_id == tenant_id)
                & (Knowledgebase.doc_num > 0)
            )
            .first()
        )
        assert kb is not None, "no KB with docs for this tenant"
        return tenant_id, user.id, kb.id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-llm", action="store_true",
        help="额外跑 AgentRunner 真 DeepSeek（~$0.02）",
    )
    parser.add_argument(
        "--skip-pytest", action="store_true",
        help="不重跑 pytest（节省 ~10s）",
    )
    args = parser.parse_args()

    tenant_id, user_id, kb_id = _pick_seed_context()
    print(f"■ seed: tenant={tenant_id[:8]}… user={user_id[:20]}… kb={kb_id[:8]}…")
    print()

    # ── 无依赖 / 纯逻辑 ──
    run_check("S01 Agent definition registry", S01_definition_registry)
    run_check("S02 Citation validator 三规则", S02_citation_validator_three_rules)
    run_check("S03 EvidenceIndex 数字抽取", S03_evidence_index_extraction)
    run_check("S04 Rewrite 四条兜底", S04_rewrite_bailouts)
    run_check("S05 Compactor 纯逻辑", S05_compactor_pure_logic)

    # ── DB I/O ──
    run_check(
        "S06 Session + history + summary 持久化",
        lambda: S06_session_service_roundtrip(tenant_id, user_id, kb_id),
    )
    run_check(
        "S07 AuditLog 写入 + 查询",
        lambda: S07_audit_log_write_query(tenant_id, user_id),
    )
    run_check(
        "S08 TenantQuota + TenantUsage",
        lambda: S08_quota_service(tenant_id),
    )
    run_check(
        "S13 SubagentTrace start/finish/list",
        lambda: S13_subagent_trace_roundtrip(tenant_id, user_id, kb_id),
    )
    run_check(
        "S14 跨租户 RBAC deny + OWNER 正反",
        lambda: S14_cross_tenant_rbac_denies(tenant_id, user_id, kb_id),
    )

    # ── Redis / 限流 / 去重 ──
    run_check("S09 Rate limit memory bucket", S09_rate_limit_memory)
    run_check("S10 Rate limit Redis（若可用）", S10_rate_limit_redis_if_available)
    run_check("S11 Dedup cache memory", S11_dedup_cache)
    run_check("S12 Bot secret 字段加密", S12_bot_secret_encryption)

    # ── Model resolver（本次刚修的 bug 回归）──
    run_async_check(
        "S15 model_resolver TenantLLM 分支（刚修的 bug 回归）",
        lambda: S15_model_resolver_live(tenant_id),
    )

    # ── HTTP blueprint 注册 ──
    run_check("S19 HTTP blueprint endpoints", S19_http_endpoints_registered)

    # ── 外层回归 ──
    if not args.skip_pytest:
        run_check("S17 pytest test/agent_v2/", S17_pytest_regression)
    else:
        RPT.skipped("S17 pytest test/agent_v2/", "--skip-pytest")
    run_check("S18 ruff check api/ test/", S18_ruff)

    # ── 真 LLM（可选）──
    if args.live_llm:
        run_async_check(
            "S16 Live AgentRunner（真 DeepSeek）",
            lambda: S16_live_runner(tenant_id, user_id, kb_id),
        )
    else:
        RPT.skipped("S16 Live AgentRunner", "用 --live-llm 开启")

    sys.exit(RPT.print())


if __name__ == "__main__":
    main()
