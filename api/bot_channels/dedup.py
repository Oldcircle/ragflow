"""消息去重：飞书等平台 3 秒内未 200 会重投。

简单实现：进程内 LRU + TTL。生产环境多实例部署时应改为 Redis SETEX。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict


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
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            if key in self._store:
                # 仍在 TTL 内 → 重复
                return False
            self._store[key] = now
            self._store.move_to_end(key)
            if len(self._store) > self._capacity:
                self._store.popitem(last=False)
            return True

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


# 全局共享（每进程一份；生产建议换 Redis）
GLOBAL_DEDUP = MessageDedupCache()
