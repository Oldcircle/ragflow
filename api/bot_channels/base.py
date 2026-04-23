"""通道适配器协议与共享数据类型。

所有 IM 平台（飞书 / 钉钉 / 企微）实现 ``BotChannelAdapter``，由
``api/apps/bot_app.py`` 的 webhook 入口按 ``channel_type`` 路由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class SignatureError(Exception):
    """webhook 签名验证失败."""


@dataclass
class InboundMessage:
    """统一的入站消息上下文（各平台 parser 把原 payload 转成这个）."""

    channel_type: str
    account_id: str
    conversation_key: str
    """会话路由键。同一 IM 会话跨多条消息保持稳定，是 bot_conversation_map 的 key."""

    im_user_id: str
    """发件人在 IM 平台上的稳定 ID（飞书 open_id、钉钉 userid 等）."""

    im_user_name: str | None = None
    text: str = ""
    quoted: str | None = None
    """引用的上文（话题父消息或回复消息）."""

    original_message_id: str = ""
    """用于回复时 reply_to_message_id 的原始消息 ID."""

    at_bot: bool = False
    """是否 @ 了机器人本身（群聊场景下机器人是否被点名）."""

    raw: dict[str, Any] = field(default_factory=dict)
    """原始 payload（留档审计用）."""


@dataclass
class OutboundReply:
    """统一的出站回复结构。"""

    text: str
    citations: list[dict] | None = None
    """引用源 [{index, doc_name, chunk_id, page?}, ...] — adapter 决定怎么排版."""

    reply_to: str | None = None
    """回复的目标消息 ID（飞书 reply_to_message_id）."""

    mentions: list[str] | None = None
    """要在回复中 @ 的用户的 IM 平台 ID."""


class BotChannelAdapter(Protocol):
    """各 IM 平台必须实现的协议。

    生命周期（webhook 一次请求）::

        1. verify_signature(headers, raw_body, config) → bool
           失败 → 401
        2. try_handle_url_challenge(payload) → dict | None
           非 None → 直接回这个 JSON（飞书 url_verification 等）
        3. parse_message(payload, config) → InboundMessage | None
           None → 200 但不进入 agent（机器人自己发的、bot_added、reaction 等都跳过）
        4. send(reply, inbound, config) → None
           Agent 跑完后回传消息
    """

    channel_type: str

    def verify_signature(
        self, headers: dict[str, str], raw_body: bytes, config: dict
    ) -> bool: ...

    def try_handle_url_challenge(self, payload: dict) -> dict | None: ...

    def parse_message(
        self, payload: dict, config: dict
    ) -> InboundMessage | None: ...

    async def send(
        self, reply: OutboundReply, inbound: InboundMessage, config: dict
    ) -> None: ...

    def get_message_id_for_dedup(self, payload: dict) -> str | None:
        """返回用于去重的稳定 message id；None 表示不去重."""
        ...
