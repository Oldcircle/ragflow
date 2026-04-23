"""飞书 Open API 轻量客户端。

只覆盖 Phase 2.2 需要的：
  - ``tenant_access_token`` 缓存（1.7 小时刷新一次，飞书 token 有效期 2 小时）
  - 发送文本消息
  - 拉取 bot 自身 open_id（解析群消息时区分是否被 @）

更复杂的功能（卡片、媒体、reaction、stream）留作 Phase 2.5。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("ragflow.bot.feishu.client")

DEFAULT_API_BASE = "https://open.feishu.cn"
TOKEN_REFRESH_BUFFER_S = 5 * 60  # 提前 5 分钟刷新


class FeishuApiError(Exception):
    def __init__(self, code: int, message: str, request: str | None = None):
        super().__init__(f"[feishu code={code}] {message}")
        self.code = code
        self.message = message
        self.request = request


class _TokenCache:
    """每个 (api_base, app_id) 一份的 tenant_access_token 缓存。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tokens: dict[str, tuple[str, float]] = {}

    @staticmethod
    def _key(api_base: str, app_id: str) -> str:
        return f"{api_base}|{app_id}"

    async def get(
        self,
        *,
        api_base: str,
        app_id: str,
        app_secret: str,
        client: httpx.AsyncClient,
    ) -> str:
        key = self._key(api_base, app_id)
        now = time.time()
        token, expire = self._tokens.get(key, ("", 0.0))
        if token and expire - now > TOKEN_REFRESH_BUFFER_S:
            return token

        async with self._lock:
            # double-check
            token, expire = self._tokens.get(key, ("", 0.0))
            if token and expire - now > TOKEN_REFRESH_BUFFER_S:
                return token
            url = f"{api_base.rstrip('/')}/open-apis/auth/v3/tenant_access_token/internal"
            r = await client.post(
                url,
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=15.0,
            )
            data: dict[str, Any] = r.json() if r.content else {}
            if data.get("code") != 0:
                raise FeishuApiError(
                    int(data.get("code", -1)),
                    str(data.get("msg") or "tenant_access_token failed"),
                    request=url,
                )
            tk = str(data.get("tenant_access_token") or "")
            ttl = int(data.get("expire") or 7200)  # 默认 2 小时
            self._tokens[key] = (tk, time.time() + ttl)
            return tk

    def invalidate(self, *, api_base: str, app_id: str) -> None:
        self._tokens.pop(self._key(api_base, app_id), None)


_TOKEN_CACHE = _TokenCache()


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict | None = None,
    params: dict | None = None,
    max_retries: int = 2,
) -> dict:
    """飞书 API 调用 + 指数退避重试。"""
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            r = await client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=30.0,
            )
            if r.status_code >= 500 and attempt < max_retries:
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            data = r.json() if r.content else {}
            return data  # type: ignore[no-any-return]
        except (httpx.NetworkError, httpx.TimeoutException) as e:
            last_err = e
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            raise
    if last_err:
        raise last_err
    return {}


async def send_text_message(
    *,
    api_base: str | None,
    app_id: str,
    app_secret: str,
    chat_id: str,
    text: str,
    reply_to_message_id: str | None = None,
    receive_id_type: str = "chat_id",
) -> dict:
    """发文本消息。

    若 ``reply_to_message_id`` 给了，调"回复消息"接口（带话题上下文）；
    否则调"发新消息"接口。
    """
    api_base = api_base or DEFAULT_API_BASE
    async with httpx.AsyncClient() as client:
        token = await _TOKEN_CACHE.get(
            api_base=api_base,
            app_id=app_id,
            app_secret=app_secret,
            client=client,
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = {"msg_type": "text", "content": json.dumps({"text": text})}

        if reply_to_message_id:
            url = f"{api_base.rstrip('/')}/open-apis/im/v1/messages/{reply_to_message_id}/reply"
            data = await _request_with_retry(
                client, "POST", url, headers=headers, json_body=body,
            )
        else:
            url = f"{api_base.rstrip('/')}/open-apis/im/v1/messages"
            send_body = {**body, "receive_id": chat_id}
            data = await _request_with_retry(
                client, "POST", url, headers=headers, json_body=send_body,
                params={"receive_id_type": receive_id_type},
            )

    if data.get("code") not in (0, None):
        # token 过期则失效缓存（飞书 token 失效 code=99991663 / 99991664）
        if data.get("code") in (99991663, 99991664):
            _TOKEN_CACHE.invalidate(api_base=api_base, app_id=app_id)
        raise FeishuApiError(
            int(data["code"]),
            str(data.get("msg") or "send_text_message failed"),
        )
    return data


async def get_bot_open_id(
    *,
    api_base: str | None,
    app_id: str,
    app_secret: str,
) -> str | None:
    """拉机器人自身 open_id（用于群消息中识别是否被 @）.

    端点：``GET /open-apis/bot/v3/info``。返回 ``data.bot.open_id``.
    """
    api_base = api_base or DEFAULT_API_BASE
    async with httpx.AsyncClient() as client:
        token = await _TOKEN_CACHE.get(
            api_base=api_base,
            app_id=app_id,
            app_secret=app_secret,
            client=client,
        )
        url = f"{api_base.rstrip('/')}/open-apis/bot/v3/info"
        data = await _request_with_retry(
            client, "GET", url,
            headers={"Authorization": f"Bearer {token}"},
        )
    if data.get("code") != 0:
        logger.warning(
            "get_bot_open_id failed: code=%s msg=%s",
            data.get("code"), data.get("msg"),
        )
        return None
    return ((data.get("data") or {}).get("bot") or {}).get("open_id")
