"""Channel adapter registry：按 channel_type 分发到具体实现."""

from __future__ import annotations

from .base import BotChannelAdapter

_REGISTRY: dict[str, BotChannelAdapter] = {}


def register_adapter(adapter: BotChannelAdapter) -> None:
    """模块导入时由各 channel package 自调；重复注册以最后一次为准."""
    _REGISTRY[adapter.channel_type] = adapter


def get_adapter(channel_type: str) -> BotChannelAdapter | None:
    return _REGISTRY.get(channel_type)


def list_supported() -> list[str]:
    return sorted(_REGISTRY.keys())
