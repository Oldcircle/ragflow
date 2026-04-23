"""飞书 ``BotChannelAdapter`` 实现。"""

from __future__ import annotations

import logging

from ..base import BotChannelAdapter, InboundMessage, OutboundReply
from .client import send_text_message
from .parser import parse_feishu_event
from .signature import verify_feishu_signature

logger = logging.getLogger("ragflow.bot.feishu.adapter")


class FeishuAdapter(BotChannelAdapter):
    channel_type = "feishu"

    # ────────────── 校验 ──────────────

    def verify_signature(
        self, headers: dict[str, str], raw_body: bytes, config: dict
    ) -> bool:
        return verify_feishu_signature(
            headers=headers,
            raw_body=raw_body,
            encrypt_key=config.get("encrypt_key"),
        )

    # ────────────── URL 验证 ──────────────

    def try_handle_url_challenge(self, payload: dict) -> dict | None:
        """飞书事件订阅首次绑定的 URL verification 回调。

        v1 challenge::

            {"type":"url_verification", "challenge":"xxx", "token":"..."}

        v2 envelope 的 challenge 也可能藏在 ``event.challenge`` 里，但绝大多数
        新建机器人都走 v1 这一路。
        """
        if not isinstance(payload, dict):
            return None
        if payload.get("type") == "url_verification" and "challenge" in payload:
            return {"challenge": payload["challenge"]}
        return None

    # ────────────── 解析 ──────────────

    def parse_message(
        self, payload: dict, config: dict
    ) -> InboundMessage | None:
        return parse_feishu_event(
            payload,
            account_id=config.get("__account_id", ""),
            session_scope=config.get("__session_scope", "group_sender"),
            bot_open_id=config.get("__bot_open_id"),
        )

    def get_message_id_for_dedup(self, payload: dict) -> str | None:
        # event_id 是飞书事件的稳定唯一 ID，比 message_id 更适合去重
        # （同一条消息重投会带相同 event_id；message_id 也稳定，备选）
        if not isinstance(payload, dict):
            return None
        header = payload.get("header") or {}
        return header.get("event_id") or (
            ((payload.get("event") or {}).get("message") or {}).get("message_id")
        )

    # ────────────── 发送 ──────────────

    async def send(
        self, reply: OutboundReply, inbound: InboundMessage, config: dict
    ) -> None:
        text = reply.text or ""
        if reply.citations:
            # 把脚注源附在回复尾部（飞书没有原生脚注组件）
            lines = ["", "—— 引用来源 ——"]
            for c in reply.citations:
                idx = c.get("index")
                doc = c.get("doc_name") or c.get("doc_id") or "?"
                page = c.get("page")
                line = f"[{idx}] {doc}"
                if page:
                    line += f" · p.{page}"
                lines.append(line)
            text = text + "\n".join(lines)

        # 飞书单条 text 上限 30 KB；保险起见 8 KB 切
        chunks = _chunk_text(text, limit=8_000)
        if not chunks:
            return

        api_base = config.get("api_base") or "https://open.feishu.cn"
        app_id = config.get("app_id") or ""
        app_secret = config.get("app_secret") or ""
        if not app_id or not app_secret:
            raise ValueError("feishu config missing app_id / app_secret")

        first_reply_to = reply.reply_to or inbound.original_message_id
        chat_id = inbound.raw.get("event", {}).get("message", {}).get("chat_id") or ""

        # 第一片段用 reply（保留话题上下文），后续用 send（新消息）
        try:
            await send_text_message(
                api_base=api_base,
                app_id=app_id,
                app_secret=app_secret,
                chat_id=chat_id,
                text=chunks[0],
                reply_to_message_id=first_reply_to,
            )
        except Exception as e:
            logger.warning(
                "feishu send_reply failed (will fall back to plain send): %s", e,
            )
            await send_text_message(
                api_base=api_base,
                app_id=app_id,
                app_secret=app_secret,
                chat_id=chat_id,
                text=chunks[0],
                reply_to_message_id=None,
            )

        for chunk in chunks[1:]:
            await send_text_message(
                api_base=api_base,
                app_id=app_id,
                app_secret=app_secret,
                chat_id=chat_id,
                text=chunk,
                reply_to_message_id=None,
            )


def _chunk_text(text: str, *, limit: int) -> list[str]:
    if not text:
        return []
    if len(text.encode("utf-8")) <= limit:
        return [text]
    out: list[str] = []
    buf = ""
    buf_bytes = 0
    for line in text.splitlines(keepends=True):
        lb = len(line.encode("utf-8"))
        if buf_bytes + lb > limit and buf:
            out.append(buf)
            buf = ""
            buf_bytes = 0
        if lb > limit:
            # 单行超长，硬切（按字符近似）
            ch_buf = ""
            for ch in line:
                if len((ch_buf + ch).encode("utf-8")) > limit:
                    out.append(ch_buf)
                    ch_buf = ch
                else:
                    ch_buf += ch
            if ch_buf:
                buf += ch_buf
                buf_bytes = len(buf.encode("utf-8"))
        else:
            buf += line
            buf_bytes += lb
    if buf:
        out.append(buf)
    return out
