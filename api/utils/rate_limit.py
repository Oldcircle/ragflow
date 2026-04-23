"""API token-bucket 限流（Phase 3.1c）。

Redis 可用时优先使用跨实例 token bucket；Redis 不可用时退回进程内 bucket。

用法::

    from api.utils.rate_limit import check_token_rate

    # 从 APIToken.token 限流（每秒桶），不够则抛 429
    check_token_rate(api_token_str, rps=20)
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time

logger = logging.getLogger("ragflow.rate_limit")

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
_REDIS_PREFIX = "rl:tb:"


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

    redis_result = _try_take_redis(key, rps)
    if redis_result is not None:
        return redis_result

    cache_key = _safe_key(key)
    capacity = max(1.0, rps)
    with _LOCK:
        b = _BUCKETS.get(cache_key)
        if b is None:
            # LRU-ish：满了就丢最老的一小批，避免异常 token 扫描把内存撑大。
            if len(_BUCKETS) >= _MAX_TRACKED_KEYS:
                for k in list(_BUCKETS.keys())[:100]:
                    _BUCKETS.pop(k, None)
            b = _BUCKETS[cache_key] = _Bucket(capacity=capacity)
        elif b.capacity != capacity:
            b.capacity = capacity
            b.tokens = min(b.tokens, capacity)
        return b.take(refill_per_sec=rps)


def check_rate(key: str, rps: float) -> None:
    """失败时抛 RateLimited；成功静默返回."""
    if not try_take(key, rps):
        raise RateLimited(key, rps)


def _safe_key(key: str) -> str:
    """把原始 token / message id 等敏感 key 摘要化后再存储."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _try_take_redis(key: str, rps: float) -> bool | None:
    """Redis token bucket；返回 None 表示不可用，应走内存兜底."""
    try:
        from rag.utils.redis_conn import REDIS_CONN
    except Exception as e:
        logger.debug("redis rate limiter import unavailable: %s", e)
        return None

    client = getattr(REDIS_CONN, "REDIS", None)
    script = getattr(REDIS_CONN, "lua_token_bucket", None)
    if client is None or script is None:
        return None

    capacity = max(1.0, rps)
    try:
        res = script(
            keys=[f"{_REDIS_PREFIX}{_safe_key(key)}"],
            args=[capacity, rps, time.time(), 1],
            client=client,
        )
        return int(res[0]) == 1
    except Exception as e:
        logger.warning("redis rate limiter unavailable, falling back to memory: %s", e)
        return None
