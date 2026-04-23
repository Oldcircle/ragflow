"""飞书会话路由键：根据 session_scope 决定 chat / sender / topic 维度的分组。

直接对应 openclaw ``extensions/feishu/src/conversation-id.ts:buildFeishuConversationId``.

scope 取值（与 openclaw 完全一致）::

    "group"               → 群里所有人共享一个 session（chat_id）
    "group_sender"        → 每个发件人在群里独立 session（chat_id:sender:open_id）
    "group_topic"         → 按话题分（chat_id:topic:root_id）
    "group_topic_sender"  → 按话题 + 发件人分

DM（chat_type=p2p）总是按发件人分；scope 仅影响 chat_type=group 的行为。
"""

from __future__ import annotations

from typing import Literal

FeishuScope = Literal[
    "group", "group_sender", "group_topic", "group_topic_sender"
]


def _norm(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    return s or None


def build_feishu_conversation_key(
    *,
    chat_type: str,
    chat_id: str,
    sender_open_id: str | None = None,
    topic_id: str | None = None,
    scope: FeishuScope = "group_sender",
    account_id: str = "",
) -> str:
    """构造跨进程稳定的会话路由键。

    返回的字符串作为 ``bot_conversation_map.conversation_key`` 存入 DB；
    带 ``feishu:<account>:`` 前缀以避免与其他渠道串号。
    """
    chat_id = _norm(chat_id) or "unknown"
    sender = _norm(sender_open_id)
    topic = _norm(topic_id)
    prefix = f"feishu:{account_id}:" if account_id else "feishu:"

    # DM (private chat) — 永远按发件人
    if chat_type in ("p2p", "private"):
        return f"{prefix}dm:{sender or chat_id}"

    # 群聊 — 按 scope 决定细分粒度
    if scope == "group_sender":
        if sender:
            return f"{prefix}group:{chat_id}:sender:{sender}"
        return f"{prefix}group:{chat_id}"
    if scope == "group_topic":
        if topic:
            return f"{prefix}group:{chat_id}:topic:{topic}"
        return f"{prefix}group:{chat_id}"
    if scope == "group_topic_sender":
        if topic and sender:
            return f"{prefix}group:{chat_id}:topic:{topic}:sender:{sender}"
        if topic:
            return f"{prefix}group:{chat_id}:topic:{topic}"
        if sender:
            return f"{prefix}group:{chat_id}:sender:{sender}"
        return f"{prefix}group:{chat_id}"
    # default: "group" — 整个群一个 session
    return f"{prefix}group:{chat_id}"
