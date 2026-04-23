"""IM 机器人渠道适配层（Phase 2.2）。

各 IM 平台（飞书 / 钉钉 / 企微 / ...）实现 ``BotChannelAdapter`` 协议；
统一在 ``api.apps.bot_app`` 的 webhook 入口被路由分发。

参考设计：openclaw 的 channel adapter 模式（``vendor/openclaw/extensions/feishu/``）。
"""

from .base import (  # noqa: F401
    BotChannelAdapter,
    InboundMessage,
    OutboundReply,
    SignatureError,
)
from .registry import get_adapter, register_adapter  # noqa: F401

# Auto-register built-in adapters on import.
from . import feishu  # noqa: F401, E402
