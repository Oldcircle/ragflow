"""进程内 token-bucket 限流（Phase 3.1c）。

同一进程内共享；多实例部署时应换成 Redis。

用法::

    from api.utils.rate_limit import check_token_rate

    # 从 APIToken.token 限流（每秒桶），不够则抛 429
    check_token_rate(api_token_str, rps=20)
"""

from __future__ import annotations

import threading
import time

# ────────────────────────────── token bucket ──────────────────────────────


class _Bucket:
    """标准 token bucket：容量 = rps；每秒补回 rps 个。"""

    __slots__ = ("capacity", "tokens", "last_refill")

    def __init__(self, capacity: float) -> None:
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()

    def take(self, refill_per_sec: float, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now
        # refill
        self.tokens = min(self.capacity, self.tokens + elapsed * refill_per_sec)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


_BUCKETS: dict[str, _Bucket] = {}
_LOCK = threading.Lock()
_MAX_TRACKED_KEYS = 10_000


class RateLimited(Exception):
    """超出速率；上层翻为 429."""

    def __init__(self, key: str, rps: float):
        super().__init__(f"rate limited: {rps}/s")
        self.key = key
        self.rps = rps


def try_take(key: str, rps: float) -> bool:
    """尝试扣一个 token；成功 True，否则 False（不抛）."""
    if not key or rps <= 0:
        return True
    with _LOCK:
        b = _BUCKETS.get(key)
        if b is None:
            # LRU-ish: 满了就丢最老的（这里简化为随便丢一个；生产换 Redis）
            if len(_BUCKETS) >= _MAX_TRACKED_KEYS:
                for k in list(_BUCKETS.keys())[:100]:
                    _BUCKETS.pop(k, None)
            b = _BUCKETS[key] = _Bucket(capacity=max(1.0, rps))
        return b.take(refill_per_sec=rps)


def check_rate(key: str, rps: float) -> None:
    """失败时抛 RateLimited；成功静默返回."""
    if not try_take(key, rps):
        raise RateLimited(key, rps)
