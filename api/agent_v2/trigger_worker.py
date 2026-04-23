"""Agent Trigger 后台 worker（Phase 3.2）。

进程内轮询 ``agent_trigger`` 表，把 ``next_run_at <= now`` 的启用 cron 触发器跑一轮。
多实例部署时应上分布式锁（Redis SETNX with TTL）避免重复执行 —— v1 用 Redis 的
``RedisDistributedLock`` 接口加一层保护。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
import uuid
from typing import Any

from api.db.services.agent_trigger_service import (
    AgentTriggerRunService,
    AgentTriggerService,
)
from common.time_utils import current_timestamp

logger = logging.getLogger("ragflow.trigger.worker")

# 扫描间隔：10s 一次对 cron 精度足够
POLL_INTERVAL_S = 10
# 单轮最多跑多少触发器（避免一次扫到上千条）
BATCH_LIMIT = 20
# 单个触发器的执行超时（秒）
RUN_TIMEOUT_S = 300
# Redis 分布式锁 key；多实例部署时同时持有此锁才跑
LOCK_KEY = "agent_trigger_scheduler"


def _redis_try_lock():
    """返回 RedisDistributedLock 实例或 None（Redis 不可用时降级，仅本进程执行）."""
    try:
        from rag.utils.redis_conn import RedisDistributedLock
        return RedisDistributedLock(LOCK_KEY, lock_value=str(uuid.uuid4()), timeout=POLL_INTERVAL_S + 5)
    except Exception as e:
        logger.debug("redis lock unavailable, running without it: %s", e)
        return None


def run_worker(stop_event: threading.Event) -> None:
    """后台线程入口。在自己的 asyncio loop 里跑."""
    logger.info("agent_trigger worker starting")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_main_loop(stop_event))
    except Exception:
        logger.exception("trigger worker crashed")
    finally:
        loop.close()
        logger.info("agent_trigger worker stopped")


async def _main_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        lock = _redis_try_lock()
        try:
            if lock is not None:
                if not lock.acquire():
                    # 其他实例持锁；睡一个周期
                    stop_event.wait(POLL_INTERVAL_S)
                    continue
            try:
                await _tick()
            finally:
                if lock is not None:
                    with contextlib.suppress(Exception):
                        lock.release()
        except Exception:
            logger.exception("trigger worker tick failed")
        stop_event.wait(POLL_INTERVAL_S)


async def _tick() -> None:
    now = current_timestamp()
    due = AgentTriggerService.due(now_ms=now, limit=BATCH_LIMIT)
    if not due:
        return
    logger.info("trigger tick: %d due", len(due))
    for t in due:
        try:
            await asyncio.wait_for(_run_one(t, kicked_by="scheduler"), timeout=RUN_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("trigger %s timed out", t.id)
            AgentTriggerService.mark_ran(t.id, status="timeout", error="exec timeout")
        except Exception as e:
            logger.exception("trigger %s failed: %s", t.id, e)
            AgentTriggerService.mark_ran(t.id, status="error", error=str(e))


async def _run_one(t, *, kicked_by: str = "manual") -> dict:
    """跑一次 trigger：Agent → Deliver → 记录结果。返回 {run_id, status, ...}."""
    from api.agent_v2.runner import AgentRunner
    from api.apps.agent_v2_app import _build_model_config
    from api.db.services.agent_v2_service import AgentV2SessionService

    run = AgentTriggerRunService.start(
        trigger_id=t.id,
        tenant_id=t.tenant_id,
        kicked_by=kicked_by,
    )
    run_id = run.id

    # 1) 拉 session 上下文
    session = AgentV2SessionService.get_by_id(t.agent_session_id)
    if not session:
        AgentTriggerRunService.finish(
            run_id, status="error",
            error=f"agent session {t.agent_session_id} not found",
        )
        AgentTriggerService.mark_ran(t.id, status="error", error="session_missing")
        return {"run_id": run_id, "status": "error"}

    try:
        model_cfg = _build_model_config(session.model_config_json, session.tenant_id)
    except Exception as e:
        AgentTriggerRunService.finish(run_id, status="error", error=f"model_config: {e}")
        AgentTriggerService.mark_ran(t.id, status="error", error=str(e))
        return {"run_id": run_id, "status": "error"}

    if not model_cfg.auth_token:
        AgentTriggerRunService.finish(
            run_id, status="error",
            error="model auth token missing; configure in Model Providers",
        )
        AgentTriggerService.mark_ran(t.id, status="error", error="no_auth_token")
        return {"run_id": run_id, "status": "error"}

    runner = AgentRunner(
        tenant_id=session.tenant_id,
        kb_ids=list(session.kb_ids or []),
        system_prompt=session.system_prompt or "",
        model=model_cfg,
        tool_names=list(session.tool_names) if session.tool_names else None,
        user_id=session.user_id,
        max_turns=t.max_turns or session.max_turns,
        max_budget_usd=t.max_budget_usd or session.max_budget_usd,
        session_id=session.id,
    )

    # 2) 跑，抓结果
    text_parts: list[str] = []
    citations: list[dict] = []
    usage_dict: dict[str, Any] = {}
    last_error: str | None = None
    try:
        async for ev in runner.run(t.prompt):
            d = ev.to_dict()
            tt = d.get("type")
            if tt == "text_delta":
                text_parts.append(d["data"].get("text", ""))
            elif tt == "tool_call_end":
                _accumulate_citations(citations, d.get("data") or {})
            elif tt == "error":
                last_error = d["data"].get("message") or d["data"].get("code")
            elif tt == "end":
                usage_dict = d["data"].get("usage") or {}
    except Exception as e:
        last_error = str(e)

    final_text = "".join(text_parts).strip()
    cost = float(usage_dict.get("total_cost_usd") or 0.0)

    # 3) 记录 usage（和其他 path 一样进 tenant_usage_daily）
    with contextlib.suppress(Exception):
        from api.db.services.tenant_quota_service import TenantUsageService
        TenantUsageService.increment(
            t.tenant_id,
            token_in=int(usage_dict.get("input_tokens") or 0),
            token_out=int(usage_dict.get("output_tokens") or 0),
            cost_usd=cost,
        )

    run_status = "error" if last_error and not final_text else (
        "success" if final_text else "error"
    )

    # 4) 投递
    delivery_status = None
    delivery_error = None
    if run_status == "success":
        try:
            delivery_status, delivery_error = await _deliver(t, final_text, citations)
        except Exception as e:
            delivery_status = "error"
            delivery_error = str(e)
            logger.exception("delivery failed for trigger %s", t.id)
    else:
        delivery_status = "skipped"

    AgentTriggerRunService.finish(
        run_id,
        status=run_status,
        result_preview=final_text or last_error or "(no output)",
        error=last_error,
        token_usage=usage_dict,
        cost_usd=cost,
        delivery_status=delivery_status,
        delivery_error=delivery_error,
    )
    AgentTriggerService.mark_ran(
        t.id, status=run_status, error=last_error,
    )
    return {"run_id": run_id, "status": run_status}


async def _deliver(t, final_text: str, citations: list[dict]) -> tuple[str, str | None]:
    """按 delivery_kind 把结果发出去。返回 (status, error)."""
    kind = t.delivery_kind or "audit_only"
    if kind == "audit_only":
        return "skipped", None

    if kind == "feishu_bot":
        cfg = t.delivery_config or {}
        bot_channel_id = cfg.get("bot_channel_id")
        chat_id = cfg.get("chat_id")
        if not bot_channel_id or not chat_id:
            return "error", "delivery_config.bot_channel_id / chat_id required"

        from api.bot_channels.feishu.client import send_text_message
        from api.db.services.bot_channel_service import BotChannelService

        bc = BotChannelService.get_by_id_for_tenant(bot_channel_id, t.tenant_id)
        if bc is None:
            return "error", "bot channel not found or not owned"

        fc = bc.config_json or {}
        app_id = fc.get("app_id") or ""
        app_secret = fc.get("app_secret") or ""
        api_base = fc.get("api_base") or None
        if not app_id or not app_secret:
            return "error", "bot channel missing app_id / app_secret"

        text = _format_delivery_text(t, final_text, citations)
        try:
            await send_text_message(
                api_base=api_base,
                app_id=app_id,
                app_secret=app_secret,
                chat_id=chat_id,
                text=text,
            )
            return "success", None
        except Exception as e:
            return "error", str(e)

    return "error", f"unsupported delivery_kind: {kind}"


def _format_delivery_text(t, final_text: str, citations: list[dict]) -> str:
    header = f"[Trigger: {t.name}]\n"
    body = final_text
    if citations:
        body += "\n\n—— 引用来源 ——"
        for c in citations[:6]:
            idx = c.get("index")
            doc = c.get("doc_name") or c.get("doc_id") or "?"
            body += f"\n[{idx}] {doc}"
    return header + body


def _accumulate_citations(out: list[dict], tool_data: dict) -> None:
    name = tool_data.get("name") or ""
    if name not in ("rag_retrieve", "rag_graph_query"):
        return
    raw = tool_data.get("result") or ""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return
    chunks = (parsed or {}).get("chunks") or []
    idx_start = len(out) + 1
    for i, c in enumerate(chunks[:6]):
        out.append({
            "index": idx_start + i,
            "doc_name": c.get("doc_name") or c.get("docnm_kwd"),
            "doc_id": c.get("doc_id"),
            "page": c.get("page"),
        })


# ──────────────────────────── 手动触发入口 ────────────────────────────


def run_manual_sync(trigger_id: str, tenant_id: str) -> dict:
    """给 HTTP 手动 run 调：同步跑一次，返回 run_id + status."""
    t = AgentTriggerService.get_by_id_for_tenant(trigger_id, tenant_id)
    if t is None:
        raise ValueError("trigger not found")
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            asyncio.wait_for(_run_one(t, kicked_by="manual"), timeout=RUN_TIMEOUT_S)
        )
    finally:
        loop.close()
