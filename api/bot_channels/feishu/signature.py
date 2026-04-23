"""飞书 webhook 签名验证（HMAC-SHA256）。

参考 openclaw 的 ``extensions/feishu/src/monitor.transport.ts:isFeishuWebhookSignatureValid``。

签名公式（飞书事件 v2 的 webhook 模式）::

    signature = sha256(timestamp + nonce + encrypt_key + raw_body_utf8)

注意：用 raw 字节做哈希；payload 不要先解析后再 JSON.stringify 重组。
"""

from __future__ import annotations

import hashlib
import hmac


def verify_feishu_signature(
    *,
    headers: dict[str, str],
    raw_body: bytes,
    encrypt_key: str | None,
) -> bool:
    """飞书事件订阅的请求签名校验。

    Returns True 即放行；False 拒绝（401）.
    没配 encrypt_key 时返回 False（强制要求签名）.
    """
    if not encrypt_key:
        return False

    # 头名称统一小写处理（quart/flask headers 大小写不敏感，但参数 dict 可能不是）
    def _h(name: str) -> str:
        return (
            headers.get(name)
            or headers.get(name.lower())
            or headers.get(name.title())
            or ""
        ).strip()

    timestamp = _h("X-Lark-Request-Timestamp")
    nonce = _h("X-Lark-Request-Nonce")
    signature = _h("X-Lark-Signature")
    if not (timestamp and nonce and signature):
        return False

    try:
        body_text = raw_body.decode("utf-8", errors="replace")
    except Exception:
        return False

    payload = (timestamp + nonce + encrypt_key + body_text).encode("utf-8")
    computed = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(computed, signature)
