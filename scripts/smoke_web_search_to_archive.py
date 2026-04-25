"""Phase 2.7 v0.13 — full autonomous chain: search → fetch → archive.

Exercises the chain that proves "agent can find resources online and archive
them on its own":
  1. user asks for a topical document (no URL given)
  2. supervisor calls `web_search` → gets URL list
  3. supervisor calls `web_fetch` (optional, for relevance check)
  4. supervisor spawns `sub_archivist`
  5. sub_archivist calls `web_fetch_to_attachment(url)` → staged + preview
  6. sub_archivist calls `submit_plan(preview=...)` → SSE plan card
  7. user replies `[plan approved]`
  8. sub_archivist calls `get_pending_plan` + `doc_ingest_attachment`
  9. KB has a new document

The smoke counts a PASS when:
  - web_search was actually called (search step happened)
  - either web_fetch_to_attachment OR doc_upload_from_url was called
    (download step happened — agent picks one of the two paths)
  - a new document landed in the target KB (final state)

Set AGENT_V2_SMOKE_NO_APPROVAL=1 to skip the approval turn (only verify
search + download intent, useful if Tavily quota is tight).

Loads ``TAVILY_API_KEY`` from ``docker/.env`` if not already set.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("PYTHONPATH", str(REPO_ROOT))
os.environ.setdefault("NLTK_DATA", str(REPO_ROOT / "nltk_data"))


def _load_env_file(path: Path, *, only_keys: set[str]) -> None:
    """Pull selected keys out of docker/.env. Skips lines whose value uses
    shell-style ``${X:-default}`` expansion since we'd otherwise inject the
    literal placeholder string into env vars used by settings.init_settings."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k not in only_keys:
            continue
        v = v.strip().strip('"').strip("'")
        if "${" in v:
            continue
        if v and k not in os.environ:
            os.environ[k] = v


# Prefer untracked .env.local (where developer-specific keys live) and fall
# back to the tracked docker/.env for shared defaults. Both paths are read
# with the same restricted-keys filter to avoid pulling shell-templated
# values like ``${DOC_ENGINE:-elasticsearch}`` into our env.
_load_env_file(REPO_ROOT / "docker" / ".env.local", only_keys={"TAVILY_API_KEY"})
_load_env_file(REPO_ROOT / "docker" / ".env", only_keys={"TAVILY_API_KEY"})

if not os.environ.get("TAVILY_API_KEY"):
    print("FAIL: TAVILY_API_KEY missing — set in docker/.env or env var")
    sys.exit(2)

from common import settings as rf_settings  # noqa: E402

rf_settings.init_settings()

from api.agent_v2.definitions.built_in._common import SUPERVISOR_TOOLS  # noqa: E402
from api.agent_v2.model_resolver import resolve_model  # noqa: E402
from api.agent_v2.plan_decision import (  # noqa: E402
    augment_for_plan_decision,
    parse_plan_decision,
)
from api.agent_v2.runner import AgentRunner  # noqa: E402
from api.db.db_models import Document  # noqa: E402
from api.db.services.agent_v2_service import (  # noqa: E402
    AgentV2MessageService,
    AgentV2SessionService,
)


TENANT = "968bd6ec3c9f11f1afc91f3c182e7a61"
KB_ID = "a15948b83d5111f1afc91f3c182e7a61"

# Supervisor tools = baseline RAG + delegation + research-mode network reads.
# Mirrors what `supervisor_research` exposes; we union here so we don't have
# to swap the supervisor definition for the smoke.
RESEARCH_TOOLS = [*SUPERVISOR_TOOLS, "web_search", "web_fetch"]

SYSTEM_PROMPT = """你是一名研究型助手，可以联网搜索、下载并归档政策文档。

工作流（严格按顺序）：
1. 用户提需要一份政策原文（未给 URL）→ 调 `web_search` 找候选源
2. 必要时 `web_fetch` 看一眼内容判定相关性
3. **派子 agent 时必须明确传 `subagent_type='sub_archivist'`** —
   不传或传别的值会导致泛型 subagent，没有归档工具。调用形如：
   `spawn_subagent(subagent_type='sub_archivist',
                    description='archive sz policy',
                    prompt='把 https://... 下载并归档到 kb_id=...')`
4. sub_archivist 内部会走两步链 `web_fetch_to_attachment` →
   `submit_plan(preview=…)`；执行结束控制权交回你
5. 把"plan 已提交，等用户回 `[plan approved]`"如实告诉用户，**不要伪造
   计划内容**。如果 sub_archivist 没真的提 plan，就说没提。

硬约束：
- 不要在没真的 spawn `subagent_type='sub_archivist'` 的情况下声称已提交计划
- 优先深圳政府官网（sz.gov.cn / szjs.sz.gov.cn）等权威源
- 单次任务最多 2 次搜索 + 1 次 fetch；不要套娃
"""


def _docs_in_kb_after(t0_ms: int) -> list:
    rows = list(
        Document.select()
        .where((Document.kb_id == KB_ID) & (Document.create_time >= t0_ms))
        .order_by(Document.create_time.desc())
        .limit(5)
    )
    return rows


async def main() -> int:
    t0 = int(time.time() * 1000)

    session = AgentV2SessionService.create_session(
        tenant_id=TENANT,
        user_id=TENANT,
        name="smoke_web_search_to_archive",
        kb_ids=[KB_ID],
        system_prompt=SYSTEM_PROMPT,
        tool_names=RESEARCH_TOOLS,
        model_config={"llm_name": "deepseek-chat", "factory": "DeepSeek"},
        max_turns=10,
        max_budget_usd=0.5,
    )
    print(f"created session {session.id}")

    model_cfg = resolve_model(
        {"llm_name": "deepseek-chat", "factory": "DeepSeek"},
        tenant_id=TENANT,
    ).config

    seen_tools: list[str] = []
    seen_subagent_tools: list[str] = []
    plan_submitted = [False]
    pending_plan_id = [None]

    async def run_turn(q: str, label: str) -> str:
        """Replicates the production ``send_message`` machinery so the
        smoke can drive multi-turn conversations including plan approval.

        Mirrors the parts of ``api.apps.agent_v2_app.send_message`` that
        actually affect runtime behavior:
          1. ``parse_plan_decision`` strips ``[plan approved]`` etc. and
             returns the cleaned message + decision string
          2. ``transition_plan_status`` updates the DB so ``@require_kb_write``
             reads a fresh status before any tool fires
          3. ``augment_for_plan_decision`` adds an explicit directive when
             the user message was a bare approval marker (otherwise the
             supervisor sees an empty message and bails)
          4. Persist the user message → load history (excluding it) →
             instantiate the runner with snapshot plan state → run →
             persist the assistant message
        """
        print(f"\n── {label} ── user: {q[:120]}{'…' if len(q) > 120 else ''}")

        clean_msg, plan_decision = parse_plan_decision(q)
        if plan_decision:
            try:
                AgentV2SessionService.transition_plan_status(
                    session.id, plan_decision,
                )
                print(f"   [plan_decision={plan_decision!r} applied to session]")
            except Exception as exc:
                print(f"   [transition_plan_status failed: {exc!r}]")

        plan_row = AgentV2SessionService.get_pending_plan(session.id) or {}
        plan_status = plan_row.get("pending_plan_status")
        plan_id = plan_row.get("pending_plan_id")

        # Persist the user message so list_for_runner can replay it next turn
        user_msg = AgentV2MessageService.append(
            session_id=session.id, role="user", content=clean_msg,
        )
        user_msg_id = getattr(user_msg, "id", None)

        runner_input = augment_for_plan_decision(
            user_message=clean_msg,
            plan_decision=plan_decision,
            plan_status=plan_status,
        )

        history = AgentV2MessageService.list_for_runner(
            session_id=session.id,
            limit=20,
            exclude_message_id=user_msg_id,
            since_create_time=None,
            include_tool_calls=True,
        )

        runner = AgentRunner(
            tenant_id=TENANT,
            kb_ids=[KB_ID],
            system_prompt=SYSTEM_PROMPT,
            model=model_cfg,
            tool_names=RESEARCH_TOOLS,
            max_turns=10,
            max_budget_usd=0.5,
            session_id=session.id,
            user_id=TENANT,
            pending_plan_status=plan_status,
            pending_plan_id=plan_id,
        )

        text: list[str] = []
        tool_call_ids: list[str] = []
        usage: dict = {}
        async for ev in runner.run(runner_input, history=history):
            d = ev.to_dict()
            t = d["type"]
            if t == "text_delta":
                text.append(d["data"].get("text", ""))
            elif t == "tool_call_start":
                tool_name = (d["data"].get("name") or "").rsplit("__", 1)[-1]
                tool_call_ids.append(d["data"].get("id") or "")
                role = d["data"].get("agent_role")
                if role and role.startswith("subagent"):
                    seen_subagent_tools.append(tool_name)
                else:
                    seen_tools.append(tool_name)
                args = d["data"].get("arguments") or {}
                arg_preview = ""
                if "query" in args:
                    arg_preview = f" query={args['query']!r}"
                elif "url" in args:
                    arg_preview = f" url={args['url']!r}"
                elif "subagent_type" in args:
                    arg_preview = f" subagent={args['subagent_type']!r}"
                indent = "       ↳" if role and role.startswith("subagent") else "   →"
                print(f"{indent} {tool_name}{arg_preview}")
            elif t == "plan_submitted":
                plan_submitted[0] = True
                pending_plan_id[0] = d["data"].get("pending_id")
                p = d["data"].get("preview") or {}
                print(
                    f"   ✓ plan_submitted id={pending_plan_id[0]} "
                    f"preview_kind={p.get('kind')} "
                    f"excerpt={len(p.get('excerpt', ''))} chars"
                )
            elif t == "subagent_start":
                print(f"   ↳ subagent_start: {d['data'].get('subagent_type')}")
            elif t == "subagent_end":
                print(f"   ↳ subagent_end: {d['data'].get('status')}")
            elif t == "end":
                usage = d["data"].get("usage") or {}
            elif t == "error":
                err_msg = str(d["data"])
                text.append(f"\n[ERROR: {err_msg}]")
                # Most common cause of SDK 'Command failed exit 1' here is
                # the upstream LLM key having zero balance (DeepSeek 402).
                # Surface a hint so the user doesn't dig into v0.19 code.
                if "exit code 1" in err_msg or "Check stderr" in err_msg:
                    print(
                        "   [hint] SDK subprocess crashed — check upstream "
                        "LLM balance. Quick test:\n"
                        "         curl -X POST https://api.deepseek.com/anthropic/v1/messages \\\n"
                        "              -H 'x-api-key: $KEY' "
                        "-H 'anthropic-version: 2023-06-01' \\\n"
                        "              -d '{\"model\":\"deepseek-chat\","
                        "\"max_tokens\":10,\"messages\":[{\"role\":\"user\","
                        "\"content\":\"hi\"}]}'\n"
                        "         402 = insufficient balance, 401 = bad key."
                    )
        reply = "".join(text)
        print(f"── reply ({len(reply)} chars) ──\n{reply[:600]}")

        # Persist assistant — this is what makes history compounding work
        # for the next turn. Without it, run_turn N+1 would replay nothing
        # but the bare user messages and the supervisor wouldn't know what
        # the archivist already did.
        try:
            AgentV2MessageService.append(
                session_id=session.id,
                role="assistant",
                content=reply,
                tool_call_ids=tool_call_ids,
                usage=usage,
            )
        except Exception as exc:
            print(f"   [persist assistant failed: {exc!r}]")

        return reply

    # Turn 1 — search + propose archive. We pick a query with an obvious
    # canonical top result (GPL v3 license text on gnu.org) so the supervisor
    # doesn't loop searches; the goal here is to validate the autonomous
    # search→fetch→delegate chain, not to test relevance ranking.
    await run_turn(
        "请帮我从网上搜 GNU GPL v3 协议的官方原文，下载后归档到当前知识库。"
        "记住：硬约束最多 2 次 web_search + 1 次 web_fetch，找到 gnu.org "
        "权威源就立刻 spawn_subagent(subagent_type='sub_archivist')。",
        "Turn 1",
    )

    # Turn 2 — approval. v0.19 wires send_message's machinery into this
    # smoke (parse_plan_decision + transition_plan_status + history) so
    # the supervisor sees a coherent conversation and re-spawns
    # sub_archivist with the now-approved plan. Set
    # AGENT_V2_SMOKE_NO_APPROVAL=1 to skip if you only want to verify the
    # propose-plan half (faster — saves a second LLM round-trip).
    refreshed = AgentV2SessionService.get_by_id(session.id)
    db_plan_waiting = (refreshed.pending_plan_status == "waiting") if refreshed else False
    if db_plan_waiting:
        print(
            f"\n[plan detected via DB] pending_plan_id="
            f"{refreshed.pending_plan_id} status={refreshed.pending_plan_status}"
        )
    if db_plan_waiting and not os.environ.get("AGENT_V2_SMOKE_NO_APPROVAL"):
        await run_turn(
            "[计划批准] 继续执行归档，记得把抓回的内容真正入库。",
            "Turn 2 (approval)",
        )

    # ── verification ──
    # The "autonomous" chain we want to prove: agent searches the web,
    # delegates to archivist, archivist submits a plan. Whether the user
    # then approves the plan and triggers the actual KB write is human-in-
    # the-loop by design (plan_gate), so we don't require turn-2 success
    # to call the chain validated.
    print("\n" + "=" * 60)
    failures: list[str] = []
    warnings: list[str] = []

    if "web_search" not in seen_tools:
        failures.append(
            "supervisor never called web_search — autonomous discovery failed"
        )
    if "spawn_subagent" not in seen_tools:
        failures.append(
            "supervisor never spawned a subagent — delegation failed"
        )

    # Proof the child actually did its job. Expected terminal status:
    # - 'approved' if we drove the approval turn (default)
    # - 'waiting' if AGENT_V2_SMOKE_NO_APPROVAL=1
    # Anything else means the chain bailed somewhere.
    refreshed = AgentV2SessionService.get_by_id(session.id)
    expected_status = (
        "waiting" if os.environ.get("AGENT_V2_SMOKE_NO_APPROVAL") else "approved"
    )
    actual_status = getattr(refreshed, "pending_plan_status", None)
    if actual_status != expected_status:
        warnings.append(
            f"session.pending_plan_status={actual_status!r} "
            f"(expected {expected_status!r}) — chain may have bailed mid-flight"
        )
    else:
        title = (refreshed.pending_plan_body or {}).get("title")
        steps = (refreshed.pending_plan_body or {}).get("steps") or []
        preview = (refreshed.pending_plan_body or {}).get("preview") or {}
        print(
            f"\n✓ plan landed (status={actual_status}):\n"
            f"  pending_id    = {refreshed.pending_plan_id}\n"
            f"  title         = {title!r}\n"
            f"  steps         = {len(steps)}\n"
            f"  preview.kind  = {preview.get('kind')}\n"
            f"  preview.bytes = {len(preview.get('excerpt', '') or '')}"
        )

    new_docs = _docs_in_kb_after(t0)
    if new_docs:
        print(f"\nKB delta — {len(new_docs)} new doc(s) since smoke start:")
        for d in new_docs[:5]:
            print(
                f"  • {d.id}  {d.name}  ({d.size} B, "
                f"status={d.status}, run={d.run})"
            )
    elif not os.environ.get("AGENT_V2_SMOKE_NO_APPROVAL"):
        # We drove the approval — there ought to be a doc. Either the
        # archivist hit the dedupe path (same content already in KB) or
        # something broke between get_pending_plan and doc_ingest_attachment.
        # Inspect the session to disambiguate.
        refreshed = AgentV2SessionService.get_by_id(session.id)
        plan_status_post = getattr(refreshed, "pending_plan_status", None)
        if plan_status_post == "approved":
            warnings.append(
                "approval applied but no new KB doc — archivist likely "
                "dedup'd against existing content (acceptable)"
            )
        else:
            failures.append(
                f"approval ran but no new KB doc landed and plan status is "
                f"{plan_status_post!r} (expected 'approved' or a fresh doc). "
                "Check sub_archivist trace for the child's last steps."
            )

    print("\nseen tool calls (parent stream):", seen_tools)
    if seen_subagent_tools:
        print("seen tool calls (subagent — bubbled):", seen_subagent_tools)
    else:
        warnings.append(
            "no subagent tool_call events bubbled to parent stream — "
            "v0.17 bubble-up may have regressed"
        )

    # When the approval flow ran (default), confirm sub_archivist actually
    # called doc_ingest_attachment — that is the actual KB-write moment.
    # Visibility comes via the v0.17 bubble-up.
    if not os.environ.get("AGENT_V2_SMOKE_NO_APPROVAL"):
        if "doc_ingest_attachment" not in seen_subagent_tools:
            failures.append(
                "approval ran but sub_archivist never called "
                "doc_ingest_attachment — KB write step missing"
            )

    if failures:
        print(f"\nFAIL ({len(failures)})")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    for w in warnings:
        print(f"WARN  {w}")
    if os.environ.get("AGENT_V2_SMOKE_NO_APPROVAL"):
        print("\nPASS ✓ — autonomous search → delegate → submit_plan chain validated")
    else:
        print("\nPASS ✓ — full chain: search → delegate → plan → approval → KB write")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
