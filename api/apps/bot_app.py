"""IM 机器人公共 webhook 入口（Phase 2.2）。

URL 前缀：``/v1/bot``（来自 ``register_page`` 自动挂载）。

公网开放路由（不要 @login_required）：

  POST /v1/bot/<channel_type>/<account_id>/events
       接收 IM 平台事件回调（飞书 / 钉钉 / 企微 ...）。
       签名验证、URL challenge、消息去重、入异步处理管道。

管理路由（只允许 tenant 内，需要登录）：

  POST /v1/bot/<channel_type>/<account_id>/test
       触发一次 token 拉取 / 自检。

  GET  /v1/bot/<channel_type>/<account_id>/conversation
       该机器人当前的活跃 IM↔Agent 会话映射。

  DELETE /v1/bot/conversation/<mapping_id>
       归档一条会话映射（强制下次重开）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from quart import request

from api.apps import current_user, login_required
from api.bot_channels.dedup import GLOBAL_DEDUP
from api.bot_channels.registry import get_adapter, list_supported
from api.db.services.audit_log_service import AuditLogService
from api.db.services.bot_channel_service import (
    BotChannelService,
    BotConversationMapService,
)
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    server_error_response,
)
from common.constants import RetCode

logger = logging.getLogger("ragflow.bot.webhook")


# ════════════════════════════════════════════════════════════════════
# 公网入口：webhook 事件接收
# ════════════════════════════════════════════════════════════════════


@manager.route("/<channel_type>/<account_id>/events", methods=["POST"])  # noqa: F821
async def receive_event(channel_type: str, account_id: str):
    """IM 平台事件回调入口。**不带任何登录态**。

    必须在 3 秒内 200，所以重活全部 ``asyncio.create_task`` 出去做。
    """
    raw_body = await request.get_data()

    adapter = get_adapter(channel_type)
    if adapter is None:
        return get_json_result(
            code=RetCode.NOT_FOUND, message=f"unsupported channel: {channel_type}"
        )

    bc = BotChannelService.find(channel_type, account_id)
    if bc is None or not bc.enabled:
        return get_json_result(
            code=RetCode.NOT_FOUND, message="channel not configured or disabled"
        )

    config = dict(bc.config_json or {})

    # 1) 签名校验（在 JSON parse 之前）
    if not adapter.verify_signature(dict(request.headers), raw_body, config):
        AuditLogService.deny(
            user_id=None,
            tenant_id=bc.tenant_id,
            action="bot.receive",
            resource_type="bot_channel",
            resource_id=bc.id,
            reason="bad_signature",
            request=request,
        )
        return get_json_result(
            code=RetCode.AUTHENTICATION_ERROR, message="invalid signature"
        )

    # 2) 解析 JSON
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        return get_data_error_result(message="invalid json body")

    # 3) URL 验证回调直接回
    challenge = adapter.try_handle_url_challenge(payload)
    if challenge is not None:
        return get_json_result(data=challenge, code=0)

    # 4) 消息去重
    msg_id = adapter.get_message_id_for_dedup(payload)
    if msg_id and not GLOBAL_DEDUP.try_mark(f"{channel_type}:{msg_id}"):
        # 重投，静默 200
        return get_json_result(data={"deduped": True}, code=0)

    # 5) 解析消息（带 account_id / scope / bot_open_id）
    config["__account_id"] = account_id
    config["__session_scope"] = bc.session_scope or "group_sender"
    # bot_open_id 一次性 lazy 缓存到内存（同 process 内）
    config["__bot_open_id"] = await _ensure_bot_open_id(bc)

    inbound = adapter.parse_message(payload, config)
    if inbound is None:
        # 非消息事件、bot 自己发的、空文本等
        return get_json_result(data={"ignored": True}, code=0)

    # 6) 入异步处理管道；webhook 立刻返 200
    asyncio.create_task(_handle_inbound(bc, inbound))
    return get_json_result(data={"queued": True}, code=0)


# ════════════════════════════════════════════════════════════════════
# 异步处理管道
# ════════════════════════════════════════════════════════════════════


async def _handle_inbound(bc, inbound):
    """收到一条入站消息后：找/建会话 → 跑 Agent → 发送回复。"""
    from api.agent_v2.runner import AgentRunner
    from api.bot_channels.base import OutboundReply
    from api.bot_channels.registry import get_adapter as _get_adapter

    try:
        # 1) 找/建 agent_v2_session
        session = _get_or_create_agent_session(bc, inbound)
        if session is None:
            logger.warning("could not get/create agent session for inbound %s",
                          inbound.original_message_id)
            return

        # 2) 跑 Agent，收尾后拿最终文本 + 引用
        from api.apps.agent_v2_app import _build_model_config

        try:
            model_cfg = _build_model_config(session.model_config_json, session.tenant_id)
        except Exception as e:
            logger.warning("model config failed: %s", e)
            await _send_fallback(bc, inbound, f"模型配置错误: {e}")
            return

        if not model_cfg.auth_token:
            await _send_fallback(bc, inbound, "模型 auth token 未配置；请在「模型供应商」添加。")
            return

        runner = AgentRunner(
            tenant_id=session.tenant_id,
            kb_ids=list(session.kb_ids or []),
            system_prompt=session.system_prompt or "",
            model=model_cfg,
            tool_names=list(session.tool_names) if session.tool_names else None,
            user_id=session.user_id,
            max_turns=session.max_turns,
            max_budget_usd=session.max_budget_usd,
        )

        text_buf: list[str] = []
        citations: list[dict] = []
        async for ev in runner.run(inbound.text):
            d = ev.to_dict()
            t = d.get("type")
            if t == "text_delta":
                text_buf.append(d["data"].get("text", ""))
            elif t == "tool_call_end":
                # 顺手抽取 rag_retrieve 的引用文档名
                _accumulate_citations_from_tool(citations, d.get("data") or {})
            elif t == "error":
                err = d["data"].get("message", "Agent error")
                await _send_fallback(bc, inbound, f"出错了: {err}")
                return

        final_text = "".join(text_buf).strip() or "（Agent 未返回有效文本）"
        adapter = _get_adapter(bc.channel_type)
        if adapter is None:
            return
        await adapter.send(
            OutboundReply(
                text=final_text,
                citations=_dedupe_citations(citations) or None,
                reply_to=inbound.original_message_id,
            ),
            inbound,
            dict(bc.config_json or {}),
        )

        AuditLogService.allow(
            user_id=f"bot:{bc.channel_type}:{inbound.im_user_id}",
            tenant_id=bc.tenant_id,
            action="bot.reply",
            resource_type="agent_v2_session",
            resource_id=session.id,
            metadata={"text_len": len(final_text), "citations": len(citations)},
        )
    except Exception:
        logger.exception("bot inbound handler failed")
        with _suppress():
            await _send_fallback(bc, inbound, "处理出错，请稍后重试。")


def _suppress():
    import contextlib
    return contextlib.suppress(Exception)


async def _send_fallback(bc, inbound, msg: str):
    from api.bot_channels.base import OutboundReply
    from api.bot_channels.registry import get_adapter as _get_adapter
    adapter = _get_adapter(bc.channel_type)
    if not adapter:
        return
    try:
        await adapter.send(
            OutboundReply(text=msg, reply_to=inbound.original_message_id),
            inbound,
            dict(bc.config_json or {}),
        )
    except Exception:
        logger.exception("fallback send failed")


def _accumulate_citations_from_tool(out: list[dict], tool_data: dict) -> None:
    """从 rag_retrieve 工具结果里抽取文档名 + chunk."""
    name = tool_data.get("name") or ""
    if name not in ("rag_retrieve", "rag_graph_query"):
        return
    raw = tool_data.get("result") or ""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return
    chunks = (parsed or {}).get("chunks") or []
    for c in chunks[:8]:
        out.append({
            "doc_name": c.get("doc_name") or c.get("docnm_kwd"),
            "doc_id": c.get("doc_id"),
            "page": c.get("page"),
        })


def _dedupe_citations(items: list[dict]) -> list[dict]:
    seen: set = set()
    result: list[dict] = []
    idx = 1
    for c in items:
        key = (c.get("doc_id"), c.get("page"))
        if key in seen:
            continue
        seen.add(key)
        result.append({**c, "index": idx})
        idx += 1
        if idx > 8:
            break
    return result


def _get_or_create_agent_session(bc, inbound):
    """根据 conversation_key 找映射；找不到则按 bc.default_* 建一个新 agent v2 session."""
    from api.db.services.agent_v2_service import AgentV2SessionService

    mapping = BotConversationMapService.find(
        channel_type=bc.channel_type,
        account_id=bc.account_id,
        conversation_key=inbound.conversation_key,
    )
    if mapping:
        # 验证 session 还活着
        sess = AgentV2SessionService.get_by_id(mapping.agent_session_id)
        if sess and sess.status != "deleted":
            BotConversationMapService.touch(mapping.id)
            return sess
        # session 被删了：丢掉旧 mapping，落个新 session
        BotConversationMapService.delete(mapping.id)

    # 新建 session（用 bot_channel 的默认配置）
    name = f"[{bc.channel_type}] {inbound.im_user_name or inbound.im_user_id[:12]}"
    sess = AgentV2SessionService.create_session(
        tenant_id=bc.tenant_id,
        user_id=bc.tenant_id,  # owner = tenant_id（bot 没真实 user）
        name=name,
        kb_ids=list(bc.default_kb_ids or []),
        system_prompt=bc.default_system_prompt or "",
        model_config=bc.default_model_config_json,
        max_turns=20,
        max_budget_usd=1.0,
    )
    BotConversationMapService.upsert(
        channel_type=bc.channel_type,
        account_id=bc.account_id,
        conversation_key=inbound.conversation_key,
        agent_session_id=sess.id,
        im_user_id=inbound.im_user_id,
        im_user_name=inbound.im_user_name,
    )
    return sess


# ════════════════════════════════════════════════════════════════════
# 管理端点
# ════════════════════════════════════════════════════════════════════


_BOT_OPEN_ID_CACHE: dict[str, str] = {}


async def _ensure_bot_open_id(bc) -> str | None:
    """lazy 拉取并进程内缓存 bot 自身 open_id；失败不阻塞主流程."""
    cache_key = f"{bc.channel_type}:{bc.account_id}"
    if cache_key in _BOT_OPEN_ID_CACHE:
        return _BOT_OPEN_ID_CACHE[cache_key]
    if bc.channel_type != "feishu":
        return None
    try:
        from api.bot_channels.feishu.client import get_bot_open_id
        cfg = bc.config_json or {}
        oid = await get_bot_open_id(
            api_base=cfg.get("api_base"),
            app_id=cfg.get("app_id") or "",
            app_secret=cfg.get("app_secret") or "",
        )
        if oid:
            _BOT_OPEN_ID_CACHE[cache_key] = oid
        return oid
    except Exception as e:
        logger.warning("get_bot_open_id failed for %s: %s", cache_key, e)
        return None


@manager.route("/<channel_type>/<account_id>/test", methods=["POST"])  # noqa: F821
@login_required
async def self_test(channel_type: str, account_id: str):
    """触发一次最小连通性自检（拉 token / open_id），用于"添加机器人"页的测试按钮."""
    try:
        bc = BotChannelService.find(channel_type, account_id)
        if bc is None:
            return get_data_error_result(message="channel not found")
        if bc.tenant_id != current_user.id:
            return get_json_result(
                code=RetCode.AUTHENTICATION_ERROR, message="not your channel"
            )
        if channel_type != "feishu":
            return get_data_error_result(message=f"unsupported channel: {channel_type}")

        from api.bot_channels.feishu.client import get_bot_open_id
        cfg = bc.config_json or {}
        oid = await get_bot_open_id(
            api_base=cfg.get("api_base"),
            app_id=cfg.get("app_id") or "",
            app_secret=cfg.get("app_secret") or "",
        )
        return get_json_result(data={"ok": True, "bot_open_id": oid})
    except Exception as e:
        logger.exception("bot self_test failed")
        return get_json_result(
            code=RetCode.EXCEPTION_ERROR, data={"ok": False}, message=str(e)
        )


@manager.route("/<channel_type>/<account_id>/conversation", methods=["GET"])  # noqa: F821
@login_required
async def list_conversations(channel_type: str, account_id: str):
    try:
        bc = BotChannelService.find(channel_type, account_id)
        if bc is None or bc.tenant_id != current_user.id:
            return get_json_result(code=RetCode.NOT_FOUND, message="not found")
        rows = BotConversationMapService.list_by_account(
            channel_type=channel_type,
            account_id=account_id,
            limit=100,
        )
        return get_json_result(data={
            "conversations": [
                {
                    "id": r.id,
                    "conversation_key": r.conversation_key,
                    "agent_session_id": r.agent_session_id,
                    "im_user_id": r.im_user_id,
                    "im_user_name": r.im_user_name,
                    "last_activity_ms": r.last_activity_ms,
                    "create_time": r.create_time,
                }
                for r in rows
            ],
        })
    except Exception as e:
        return server_error_response(e)


@manager.route("/conversation/<mapping_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def revoke_conversation(mapping_id: str):
    try:
        # 间接验权：只允许 tenant_id == current_user.id 的 channel 下的 mapping
        from api.db.db_models import BotConversationMap
        mapping = BotConversationMap.get_or_none(BotConversationMap.id == mapping_id)
        if mapping is None:
            return get_json_result(data={"deleted": 0})
        bc = BotChannelService.find(mapping.channel_type, mapping.account_id)
        if bc is None or bc.tenant_id != current_user.id:
            return get_json_result(
                code=RetCode.AUTHENTICATION_ERROR, message="not your channel"
            )
        deleted = BotConversationMapService.delete(mapping_id)
        return get_json_result(data={"deleted": deleted})
    except Exception as e:
        return server_error_response(e)


@manager.route("/_supported", methods=["GET"])  # noqa: F821
async def supported_channels():
    """前端 add-bot 页问支持哪几种渠道（公开端点）."""
    return get_json_result(data={"channels": list_supported()})
