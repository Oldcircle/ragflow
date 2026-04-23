"""飞书事件 payload → ``InboundMessage`` 转换。

飞书事件 v2 envelope::

    {
      "schema": "2.0",
      "header": {
        "event_id": "...",            # 用于去重
        "event_type": "im.message.receive_v1",
        "create_time": "...",
        "token": "...",                # verification_token
        "app_id": "...",
        "tenant_key": "..."
      },
      "event": {
        "sender": { sender_id: { open_id, user_id, union_id }, sender_type, ... },
        "message": {
          "message_id": "om_xxx",
          "root_id": "om_xxx",         # 话题父 / 回复目标
          "parent_id": "om_xxx",
          "thread_id": "...",
          "chat_id": "oc_xxx",
          "chat_type": "p2p" | "group" | "private",
          "message_type": "text" | "post" | "image" | ...,
          "content": '{"text": "<at user_id=\\"ou_xxx\\">@bot</at> 你好"}',
          "mentions": [
            {"key": "@_user_1", "id": {"open_id": "ou_xxx", ...}, "name": "Bot"}
          ],
          "create_time": "..."
        }
      }
    }

只处理 ``im.message.receive_v1``；其他事件返回 None.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..base import InboundMessage
from .conversation import build_feishu_conversation_key

logger = logging.getLogger("ragflow.bot.feishu.parser")

_MENTION_TAG_RE = re.compile(r'<at[^>]*?(?:user_id|id)=(?:"|\\")([^"\\]+)(?:"|\\")[^>]*?>([^<]*)</at>')


def _safe_load_content(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        try:
            # 飞书有时把 content 二次转义
            return json.loads(raw.replace('\\"', '"'))
        except Exception:
            return {}


def _extract_text(content: dict[str, Any], message_type: str) -> str:
    """各 message_type 的正文提取。文本/post/卡片回复都尽量回到字符串."""
    if message_type == "text":
        return str(content.get("text") or "")

    if message_type == "post":
        # post 是富文本，flatten 成一行文本（v1 不做完整保真）
        chunks: list[str] = []
        title = content.get("title") or ""
        if title:
            chunks.append(str(title))
        for line in content.get("content") or []:
            if not isinstance(line, list):
                continue
            for seg in line:
                if not isinstance(seg, dict):
                    continue
                tag = seg.get("tag")
                if tag == "text":
                    chunks.append(str(seg.get("text") or ""))
                elif tag == "a":
                    chunks.append(f"{seg.get('text', '')} ({seg.get('href', '')})")
                elif tag == "at":
                    chunks.append(f"@{seg.get('user_name') or seg.get('user_id') or ''}")
        return "\n".join(chunks).strip()

    if message_type == "image":
        return "[图片]"
    if message_type == "file":
        return f"[文件: {content.get('file_name') or 'attachment'}]"
    if message_type == "audio":
        return "[语音消息]"

    # fallback
    return str(content.get("text") or "")


def _strip_bot_mention(text: str, bot_open_id: str | None) -> tuple[str, bool]:
    """去掉 @bot 标记，返回 (clean_text, was_mentioned)."""
    if not text:
        return "", False

    mentioned = False

    def repl(m: re.Match[str]) -> str:
        nonlocal mentioned
        target_id = m.group(1)
        if bot_open_id and target_id == bot_open_id:
            mentioned = True
        return ""  # 全去掉，AI 不需要看到 @标签

    cleaned = _MENTION_TAG_RE.sub(repl, text).strip()
    return cleaned, mentioned


def parse_feishu_event(
    payload: dict[str, Any],
    *,
    account_id: str,
    session_scope: str = "group_sender",
    bot_open_id: str | None = None,
) -> InboundMessage | None:
    """飞书事件 → InboundMessage；非消息事件返回 None。"""
    header = payload.get("header") or {}
    event_type = header.get("event_type")
    if event_type != "im.message.receive_v1":
        # 仅支持消息接收事件；其他（机器人被加入、菜单点击等）后续再扩展
        return None

    event = payload.get("event") or {}
    msg = event.get("message") or {}
    sender = event.get("sender") or {}
    sender_id_obj = sender.get("sender_id") or {}

    # 跳过机器人自己发的（避免回声）
    sender_type = sender.get("sender_type") or ""
    if sender_type == "app":
        return None

    chat_id = msg.get("chat_id") or ""
    chat_type = msg.get("chat_type") or "group"
    message_id = msg.get("message_id") or ""
    if not chat_id or not message_id:
        return None

    open_id = sender_id_obj.get("open_id") or ""
    user_id = sender_id_obj.get("user_id") or ""
    sender_stable_id = open_id or user_id or "unknown"

    raw_content = msg.get("content") or ""
    content_obj = _safe_load_content(raw_content)
    raw_text = _extract_text(content_obj, msg.get("message_type") or "text")
    clean_text, mention_in_body = _strip_bot_mention(raw_text, bot_open_id)

    # mention 列表里如果有 bot_open_id 也算被点名（有些发送方式不会把 <at> 标签写在 text 里）
    at_bot = mention_in_body
    if not at_bot and bot_open_id:
        for m in (msg.get("mentions") or []):
            mid = (m or {}).get("id") or {}
            if mid.get("open_id") == bot_open_id:
                at_bot = True
                break

    topic_id = msg.get("root_id") or msg.get("thread_id") or None
    conversation_key = build_feishu_conversation_key(
        chat_type=chat_type,
        chat_id=chat_id,
        sender_open_id=open_id,
        topic_id=topic_id,
        scope=session_scope,  # type: ignore[arg-type]
        account_id=account_id,
    )

    # DM 默认就是 @bot 的语义；群里没显式 @ 则不应触发回复（避免吵）
    if chat_type in ("p2p", "private"):
        at_bot = True

    if not clean_text:
        return None

    return InboundMessage(
        channel_type="feishu",
        account_id=account_id,
        conversation_key=conversation_key,
        im_user_id=sender_stable_id,
        im_user_name=None,  # 飞书事件 payload 不包含发件人名；如需可走另一个 API 拉
        text=clean_text,
        quoted=None,  # v1 暂不抓 root_id 对应正文（多一次 API 调用）
        original_message_id=message_id,
        at_bot=at_bot,
        raw=payload,
    )
