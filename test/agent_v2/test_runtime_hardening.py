import sys
import types

from api.bot_channels.dedup import MessageDedupCache
from api.utils import rate_limit


def test_rate_limit_uses_redis_token_bucket_with_hashed_key(monkeypatch):
    calls = []

    def fake_token_bucket(*, keys, args, client):
        calls.append((keys, args, client))
        return [1, 19.0]

    fake_client = object()
    fake_conn = types.SimpleNamespace(
        REDIS=fake_client,
        lua_token_bucket=fake_token_bucket,
    )
    monkeypatch.setitem(
        sys.modules,
        "rag.utils.redis_conn",
        types.SimpleNamespace(REDIS_CONN=fake_conn),
    )

    assert rate_limit.try_take("apitoken:secret-token", 20.0) is True

    [(keys, args, client)] = calls
    assert client is fake_client
    assert keys[0].startswith("rl:tb:")
    assert "secret-token" not in keys[0]
    assert args[0] == 20.0
    assert args[1] == 20.0
    assert args[3] == 1


def test_rate_limit_falls_back_to_memory_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(rate_limit, "_try_take_redis", lambda _key, _rps: None)

    assert rate_limit.try_take("apitoken:memory-only", 1.0) is True
    assert rate_limit.try_take("apitoken:memory-only", 1.0) is False


def test_message_dedup_uses_redis_set_nx_ex_with_hashed_key(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.keys: set[str] = set()
            self.calls = []

        def set(self, key, value, ex=None, nx=False):
            self.calls.append((key, value, ex, nx))
            if nx and key in self.keys:
                return False
            self.keys.add(key)
            return True

    fake_redis = FakeRedis()
    fake_conn = types.SimpleNamespace(REDIS=fake_redis)
    monkeypatch.setitem(
        sys.modules,
        "rag.utils.redis_conn",
        types.SimpleNamespace(REDIS_CONN=fake_conn),
    )

    cache = MessageDedupCache(ttl_seconds=123)

    assert cache.try_mark("feishu:message-secret") is True
    assert cache.try_mark("feishu:message-secret") is False

    key, value, ex, nx = fake_redis.calls[0]
    assert key.startswith("bot:dedup:")
    assert "message-secret" not in key
    assert value == "1"
    assert ex == 123
    assert nx is True


def test_message_dedup_falls_back_to_memory_when_redis_unavailable(monkeypatch):
    cache = MessageDedupCache(ttl_seconds=600)
    monkeypatch.setattr(cache, "_try_mark_redis", lambda _key: None)

    assert cache.try_mark("feishu:memory-message") is True
    assert cache.try_mark("feishu:memory-message") is False
