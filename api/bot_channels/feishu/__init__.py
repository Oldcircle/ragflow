"""飞书机器人适配器（Phase 2.2）。

参考：``vendor/openclaw/extensions/feishu/``。
"""

from __future__ import annotations

from ..registry import register_adapter
from .adapter import FeishuAdapter

# Register on import so api/bot_channels/__init__.py can `from . import feishu`
register_adapter(FeishuAdapter())
