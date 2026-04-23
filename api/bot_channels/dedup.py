"""消息去重：飞书等平台 3 秒内未 200 会重投。

Redis 可用时使用 SET NX EX 做跨实例去重；Redis 不可用时退回进程内 LRU + TTL。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict

logger = logging.getLogger("ragflow.bot.dedup")

_REDIS_PREFIX = "bot:dedup:"


class MessageDedupCache:
    """线程安全的 LRU + TTL 缓存。

    用法::

        cache = MessageDedupCache(capacity=10_000, ttl_seconds=600)
        if not cache.try_mark(message_id):
            return  # 已处理过，丢弃
    """

    def __init__(self, capacity: int = 10_000, ttl_seconds: int = 600):
        self._capacity = capacity
        self._ttl = ttl_seconds
        self._store: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def try_mark(self, key: str) -> bool:
        """如果是新 key 标记并返回 True；如果已存在（且未过期）返回 False."""
        if not key:
            return True  # 不去重也比误丢好
        redis_result = self._try_mark_redis(key)
        if redis_result is not None:
            return redis_result
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            cache_key = _safe_key(key)
            if cache_key in self._store:
                # 仍在 TTL 内 → 重复
                return False
            self._store[cache_key] = now
            self._store.move_to_end(cache_key)
            if len(self._store) > self._capacity:
                self._store.popitem(last=False)
            return True

    def _try_mark_redis(self, key: str) -> bool | None:
        """Redis SET NX EX；返回 None 表示不可用，应走内存兜底."""
        try:
            from rag.utils.redis_conn import REDIS_CONN
        except Exception as e:
            logger.debug("redis dedup import unavailable: %s", e)
            return None

        client = getattr(REDIS_CONN, "REDIS", None)
        if client is None:
            return None

        try:
            return bool(client.set(f"{_REDIS_PREFIX}{_safe_key(key)}", "1", ex=self._ttl, nx=True))
        except Exception as e:
            logger.warning("redis dedup unavailable, falling back to memory: %s", e)
            return None

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._ttl
        # 由于 OrderedDict 按插入序，过期的都在前面（FIFO 近似）
        keys_to_drop = []
        for k, ts in self._store.items():
            if ts < cutoff:
                keys_to_drop.append(k)
            else:
                break
        for k in keys_to_drop:
            self._store.pop(k, None)


# 全局共享；Redis 可用时跨实例，Redis 不可用时每进程一份内存兜底。
GLOBAL_DEDUP = MessageDedupCache()


def _safe_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
