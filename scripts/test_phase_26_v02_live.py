"""Phase 2.6 v0.2 活体验证脚本 — 真跑 LLM，观察 sub_librarian / sub_archivist 是否
按设计闭环工作。

**不跑 pytest 能验到的事**：
- LLM 真的识别出该派 sub_librarian（based on `when_to_use` + description）
- kb_audit 的真实响应形状是否足够让 LLM 做决策
- doc_create_note 是否真能落一份新 doc（content_hash dedup 生效）
- submit_plan 是否在该出现的地方出现
- audit log 是否真按预期落行

**用法**：
    cd ~/Opensource/forks/ragflow
    PYTHONPATH=$(pwd) NLTK_DATA=./nltk_data \\
      .venv/bin/python scripts/test_phase_26_v02_live.py

每跑一次用 ~$0.10-0.30（DeepSeek）。清理在结束时。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import warnings
from dataclasses import dataclass

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    try:
        import xgboost  # noqa: F401
    except Exception:
        pass

from common import settings  # noqa: E402

settings.init_settings()


# ───────── seed helpers ─────────


def _seed_context() -> tuple[str, str, str]:
    """拿 qsjzqdnr@gmail.com 的 tenant / user / 保障房 kb_id。"""
    from api.db.db_models import DB, Knowledgebase, User, UserTenant

    with DB.connection_context():
        user = User.select().where(User.email == "qsjzqdnr@gmail.com").get()
        t = (
            UserTenant.select()
            .where((UserTenant.user_id == user.id) & (UserTenant.status == "1"))
            .first()
        )
        tenant_id = t.tenant_id if t else user.id
        kb = (
            Knowledgebase.select()
            .where((Knowledgebase.tenant_id == tenant_id) & (Knowledgebase.doc_num > 0))
            .first()
        )
        assert kb is not None, "no KB with docs"
        return tenant_id, user.id, kb.id


# ───────── observability ─────────


@dataclass
class Observation:
    """每轮捕获的运行画像。"""

    event_count: int = 0
    text_len: int = 0
    tools_called: list[str] = None
    subagents_spawned: list[str] = None
    questions_asked: list[str] = None
    plans_submitted: list[str] = None
    citation_warnings: int = 0
    end_usage: dict | None = None
    final_text: str = ""
    errors: list[str] = None

    def __post_init__(self):
        self.tools_called = self.tools_called or []
        self.subagents_spawned = self.subagents_spawned or []
        self.questions_asked = self.questions_asked or []
        self.plans_submitted = self.plans_submitted or []
        self.errors = self.errors or []


async def _run_one(
    tenant_id: str,
    user_id: str,
    kb_id: str,
    prompt: str,
    supervisor_def_name: str = "sz-baojian-house",
    max_turns: int = 12,
    max_budget: float = 0.6,
    citation_enforce: str = "warn",
) -> tuple[Observation, str]:
    """跑一次 supervisor，返回观察报告 + session_id 便于清理。"""
    from api.agent_v2.definitions import get_definition, resolve_tools
    from api.agent_v2.definitions.registry import clear_cache_for_tests
    from api.agent_v2.model_resolver import resolve_model
    from api.agent_v2.runner import AgentRunner
    from api.db.services.agent_v2_service import (
        AgentV2MessageService,
        AgentV2SessionService,
    )

    clear_cache_for_tests()
    defn = get_definition(supervisor_def_name)
    assert defn is not None, f"supervisor {supervisor_def_name!r} not registered"

    # 用 definition 的显式 tools 列表（Phase 2.6 v0.2 之后 supervisor 不再是 "*"）
    supervisor_tool_names = resolve_tools(defn, parent_tools=None)
    # supervisor 不拿 write / audit 工具，必须通过 spawn_subagent(subagent_type=...) 派
    # librarian 或 archivist 来访问

    resolved = resolve_model(
        {"llm_name": "deepseek-chat", "factory": "DeepSeek"}, tenant_id,
    )

    sys_prompt = defn.resolve_system_prompt({"kb_ids": [kb_id]})
    # 追加一条 Phase 2.6 v0.2 专属提示，帮 supervisor 知道现在有 2 个 subagent
    sys_prompt += (
        "\n\n---\n【可派 subagent】\n"
        "  - sub_librarian：体检 KB + 写笔记 / 报告。用户要『整理 / 了解 / "
        "总结 / 写报告』时派它\n"
        "  - sub_archivist：真正执行打标签 / 归档 / 重命名 / 重解析 / 入库。"
        "用户明确要『做 / 执行』动作时派它\n"
        "  - 组合用：先让 librarian 看 + 写建议笔记，然后 archivist 照着笔记执行\n"
    )

    s = AgentV2SessionService.create_session(
        tenant_id=tenant_id, user_id=user_id,
        name="__phase26v02_live__",
        kb_ids=[kb_id],
        system_prompt=sys_prompt,
        model_config={"llm_name": "deepseek-chat", "factory": "DeepSeek"},
        max_turns=max_turns,
        max_budget_usd=max_budget,
        citation_enforce_level=citation_enforce,
    )

    obs = Observation()
    try:
        runner = AgentRunner(
            tenant_id=tenant_id, user_id=user_id, kb_ids=[kb_id],
            system_prompt=sys_prompt,
            model=resolved.config,
            tool_names=supervisor_tool_names,
            max_turns=max_turns,
            max_budget_usd=max_budget,
            session_id=s.id,
            citation_enforce_level=citation_enforce,
        )

        text_parts: list[str] = []
        async for ev in runner.run(prompt):
            obs.event_count += 1
            d = ev.to_dict()
            t = d["type"]
            if t == "text_delta":
                text_parts.append(d["data"].get("text") or "")
            elif t == "tool_call_start":
                name = d["data"].get("name") or ""
                # 剥 mcp 前缀
                short = name.rsplit("__", 1)[-1]
                obs.tools_called.append(short)
            elif t == "subagent_start":
                obs.subagents_spawned.append(d["data"].get("description") or "")
            elif t == "ask_user_question":
                obs.questions_asked.append(d["data"].get("question") or "")
            elif t == "plan_submitted":
                obs.plans_submitted.append(d["data"].get("title") or "")
            elif t == "citation_warning":
                obs.citation_warnings += 1
            elif t == "end":
                obs.end_usage = d["data"].get("usage") or {}
            elif t == "error":
                obs.errors.append(
                    f"{d['data'].get('code')}: {d['data'].get('message')}"
                )

        obs.final_text = "".join(text_parts).strip()
        obs.text_len = len(obs.final_text)
    finally:
        # 不落库 assistant 消息（跳过 compactor 写入），直接软删 session
        try:
            AgentV2MessageService.append(
                session_id=s.id, role="assistant", content=obs.final_text[:4000],
            )
        except Exception:
            pass

    return obs, s.id


def _cleanup_session(session_id: str) -> None:
    try:
        from api.db.services.agent_v2_service import AgentV2SessionService

        AgentV2SessionService.soft_delete(session_id)
    except Exception:
        pass


def _delete_new_docs(tenant_id: str, kb_id: str, since_ms: int) -> int:
    """Librarian 生成的笔记用 tag=source:agent_note 标记，删掉测试新建的。"""
    from api.db.db_models import Document
    from api.db.services.document_service import DocumentService

    try:
        docs = list(
            Document.select(Document.id)
            .where(
                (Document.kb_id == kb_id)
                & (Document.create_time >= since_ms)
                & (Document.source_type == "local")
            )
        )
        cleaned = 0
        for d in docs:
            try:
                ok, doc = DocumentService.get_by_id(d.id)
                if ok and doc:
                    DocumentService.remove_document(doc, tenant_id)
                    cleaned += 1
            except Exception:
                pass
        return cleaned
    except Exception:
        return 0


# ───────── scenarios ─────────


SCENARIOS = [
    {
        "id": "A1-readonly-audit",
        "prompt": (
            "帮我看看这个保障房知识库现在是什么状态？"
            "文档多不多，有没有陈旧或者有问题的？总结几句话告诉我就行，不用入库。"
        ),
        "expect": [
            # Supervisor 应该派 sub_librarian
            "spawn_subagent",
        ],
        "hint": "纯只读场景：LLM 应派 librarian 调 kb_audit / kb_stats，不做任何写操作",
    },
    {
        "id": "A2-write-note",
        "prompt": (
            "给这个知识库做一次体检，然后把结果存成一份新笔记，"
            "标题叫『2026-04-24 · 知识库体检报告』。"
        ),
        "expect": [
            "spawn_subagent",  # 派 librarian
            "doc_create_note",  # librarian 写笔记入库
        ],
        "hint": "观察 librarian → kb_audit → doc_create_note 的闭环",
    },
    {
        "id": "A3-question-fallback",
        "prompt": "帮我把政策类文档归档一下。",
        "expect": [
            # 用户没说归到哪个 KB —— archivist / supervisor 应主动 ask_user_question
        ],
        "hint": "故意模糊指令；期望 Agent 先 ask_user_question 澄清再动手",
    },
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario", choices=["A1", "A2", "A3", "all"], default="A1",
        help="A1 只读体检 / A2 写笔记闭环 / A3 澄清场景 / all 全部",
    )
    parser.add_argument("--keep", action="store_true",
                        help="不清理新建的文档和 session")
    args = parser.parse_args()

    if not (os.environ.get("AGENT_V2_DEEPSEEK_KEY") or
            os.environ.get("DEEPSEEK_API_KEY")):
        # fallback 到 DB 里的 tenant_llm 配置（resolve_model 会接住）
        pass

    tenant_id, user_id, kb_id = _seed_context()
    since_ms = int(time.time() * 1000)
    print(f"■ seed: tenant={tenant_id[:8]} user={user_id[:20]} kb={kb_id[:8]}")
    print()

    picks = (
        [s for s in SCENARIOS if s["id"].startswith(args.scenario)]
        if args.scenario != "all"
        else SCENARIOS
    )

    results: list[dict] = []
    session_ids: list[str] = []

    for sc in picks:
        print("═" * 72)
        print(f"▶ Scenario {sc['id']}: {sc['hint']}")
        print(f"  Prompt: {sc['prompt']}")
        print("─" * 72)

        t0 = time.time()
        try:
            obs, sid = await _run_one(
                tenant_id, user_id, kb_id, sc["prompt"],
            )
            session_ids.append(sid)
        except Exception as exc:
            import traceback
            traceback.print_exc(limit=5)
            results.append({
                "scenario": sc["id"],
                "outcome": "CRASH",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        elapsed = int((time.time() - t0) * 1000)

        expected = set(sc.get("expect") or [])
        seen_tools = set(obs.tools_called)
        missing = expected - seen_tools
        outcome = "PASS" if not missing and not obs.errors else "FAIL"
        if missing:
            outcome = "MISS"

        print(f"  Outcome   : {outcome}")
        print(f"  Duration  : {elapsed} ms")
        print(f"  Events    : {obs.event_count}")
        print(f"  Text len  : {obs.text_len}")
        print(f"  Tools     : {obs.tools_called}")
        print(f"  Subagents : {obs.subagents_spawned}")
        print(f"  Questions : {[q[:50] for q in obs.questions_asked]}")
        print(f"  Plans     : {obs.plans_submitted}")
        print(f"  CitationW : {obs.citation_warnings}")
        if obs.errors:
            print(f"  Errors    : {obs.errors}")
        if missing:
            print(f"  Missing   : {sorted(missing)}")
        if obs.end_usage:
            cost = obs.end_usage.get("total_cost_usd")
            print(f"  Cost      : ${cost!r}" if cost else "")
        print()
        print("  Final text (first 400 chars):")
        print("  " + obs.final_text[:400].replace("\n", "\n  "))
        print()
        results.append({
            "scenario": sc["id"],
            "outcome": outcome,
            "elapsed_ms": elapsed,
            "tools": obs.tools_called,
            "subagents": obs.subagents_spawned,
            "questions": len(obs.questions_asked),
            "plans": len(obs.plans_submitted),
            "citation_warnings": obs.citation_warnings,
            "text_len": obs.text_len,
            "errors": obs.errors,
            "cost_usd": (obs.end_usage or {}).get("total_cost_usd"),
        })

    # 清理
    if not args.keep:
        print("═" * 72)
        print("Cleanup:")
        cleaned = _delete_new_docs(tenant_id, kb_id, since_ms)
        print(f"  Deleted {cleaned} newly-created documents")
        for sid in session_ids:
            _cleanup_session(sid)
        print(f"  Soft-deleted {len(session_ids)} sessions")

    # 汇总
    print("═" * 72)
    print("Summary")
    print("═" * 72)
    for r in results:
        mark = {"PASS": "✅", "FAIL": "❌", "MISS": "⚠️", "CRASH": "💥"}[r["outcome"]]
        tools = ",".join(r.get("tools") or [])[:80]
        print(f"  {mark} {r['scenario']}  tools=[{tools}]  "
              f"subagent={len(r.get('subagents') or [])}  "
              f"q={r['questions']}  plan={r['plans']}  "
              f"cw={r['citation_warnings']}  "
              f"t={r['elapsed_ms']}ms  $={r.get('cost_usd')}")

    total = len(results)
    passed = sum(1 for r in results if r["outcome"] == "PASS")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
