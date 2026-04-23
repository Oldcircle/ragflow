"""IM 机器人渠道与会话映射服务（Phase 2.2）。"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

from peewee import DoesNotExist

from api.db.db_models import DB, BotChannel, BotConversationMap
from api.db.services.common_service import CommonService
from common import settings
from common.crypto_utils import CryptoUtil
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp

logger = logging.getLogger("ragflow.bot.channel")

SENSITIVE_CONFIG_KEYS = {"app_secret", "encrypt_key", "verification_token"}
_ENC_PREFIX = "enc:v1:"


def _secret_crypto() -> CryptoUtil | None:
    key = os.environ.get("RAGFLOW_BOT_CHANNEL_SECRET_KEY") or settings.SECRET_KEY
    if not key:
        logger.warning("bot channel secret encryption disabled: no key configured")
        return None
    return CryptoUtil(algorithm="aes-256-cbc", key=str(key))


def _encrypt_value(value: Any) -> Any:
    if value is None:
        return value
    text = str(value)
    if not text or text.startswith(_ENC_PREFIX):
        return value
    crypto = _secret_crypto()
    if crypto is None:
        return value
    encrypted = crypto.encrypt(text.encode("utf-8"))
    return _ENC_PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")


def _decrypt_value(value: Any) -> Any:
    if not isinstance(value, str) or not value.startswith(_ENC_PREFIX):
        return value
    crypto = _secret_crypto()
    if crypto is None:
        return value
    try:
        raw = base64.urlsafe_b64decode(value[len(_ENC_PREFIX):].encode("ascii"))
        return crypto.decrypt(raw).decode("utf-8")
    except Exception:
        logger.exception("failed to decrypt bot channel secret")
        return ""


def encrypt_config_secrets(config: dict | None) -> dict:
    cfg = dict(config or {})
    for key in SENSITIVE_CONFIG_KEYS:
        if cfg.get(key):
            cfg[key] = _encrypt_value(cfg[key])
    return cfg


def decrypt_config_secrets(config: dict | None) -> dict:
    cfg = dict(config or {})
    for key in SENSITIVE_CONFIG_KEYS:
        if cfg.get(key):
            cfg[key] = _decrypt_value(cfg[key])
    return cfg


def merge_config_update(existing: dict | None, incoming: dict | None) -> dict:
    """Merge admin updates while preserving omitted/placeholder secrets."""
    merged = decrypt_config_secrets(existing)
    for key, value in dict(incoming or {}).items():
        if key in SENSITIVE_CONFIG_KEYS:
            text = str(value or "").strip()
            if text and "***" not in text:
                merged[key] = text
        else:
            merged[key] = value
    return encrypt_config_secrets(merged)


def _with_decrypted_config(row: BotChannel | None) -> BotChannel | None:
    if row is not None:
        row.config_json = decrypt_config_secrets(row.config_json)
    return row


class BotChannelService(CommonService):
    model = BotChannel

    # ────────────── CRUD by tenant/admin ──────────────

    @classmethod
    @DB.connection_context()
    def list_by_tenant(cls, tenant_id: str) -> list[BotChannel]:
        rows = list(
            BotChannel.select().where(BotChannel.tenant_id == tenant_id)
            .order_by(BotChannel.create_time.desc())
        )
        return [_with_decrypted_config(r) for r in rows if r is not None]

    @classmethod
    @DB.connection_context()
    def find(cls, channel_type: str, account_id: str) -> BotChannel | None:
        try:
            row = BotChannel.select().where(
                (BotChannel.channel_type == channel_type)
                & (BotChannel.account_id == account_id)
            ).get()
            return _with_decrypted_config(row)
        except DoesNotExist:
            return None

    @classmethod
    @DB.connection_context()
    def get_by_id_for_tenant(cls, channel_id: str, tenant_id: str) -> BotChannel | None:
        try:
            row = BotChannel.select().where(
                (BotChannel.id == channel_id) & (BotChannel.tenant_id == tenant_id)
            ).get()
            return _with_decrypted_config(row)
        except DoesNotExist:
            return None

    @classmethod
    @DB.connection_context()
    def create(
        cls,
        *,
        tenant_id: str,
        channel_type: str,
        account_id: str,
        name: str,
        config_json: dict,
        default_kb_ids: list[str] | None = None,
        default_agent_template_id: str | None = None,
        default_model_config_json: dict | None = None,
        default_system_prompt: str = "",
        session_scope: str = "group_sender",
        enabled: bool = True,
    ) -> BotChannel:
        if cls.find(channel_type, account_id):
            raise ValueError(
                f"bot channel already exists: {channel_type}/{account_id}"
            )
        row = BotChannel.create(
            id=get_uuid(),
            tenant_id=tenant_id,
            channel_type=channel_type,
            account_id=account_id,
            name=name,
            config_json=encrypt_config_secrets(config_json),
            default_kb_ids=list(default_kb_ids or []),
            default_agent_template_id=default_agent_template_id,
            default_model_config_json=default_model_config_json,
            default_system_prompt=default_system_prompt or "",
            session_scope=session_scope,
            enabled=1 if enabled else 0,
        )
        return _with_decrypted_config(row)

    @classmethod
    @DB.connection_context()
    def update(cls, channel_id: str, tenant_id: str, fields: dict[str, Any]) -> int:
        allowed = {
            "name", "config_json", "default_kb_ids",
            "default_agent_template_id", "default_model_config_json",
            "default_system_prompt", "session_scope", "enabled",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        if "enabled" in clean:
            clean["enabled"] = 1 if clean["enabled"] else 0
        if "config_json" in clean:
            existing = BotChannel.select(BotChannel.config_json).where(
                (BotChannel.id == channel_id) & (BotChannel.tenant_id == tenant_id)
            ).first()
            clean["config_json"] = merge_config_update(
                existing.config_json if existing else {},
                clean["config_json"],
            )
        if not clean:
            return 0
        return BotChannel.update(**clean).where(
            (BotChannel.id == channel_id) & (BotChannel.tenant_id == tenant_id)
        ).execute()

    @classmethod
    @DB.connection_context()
    def delete(cls, channel_id: str, tenant_id: str) -> int:
        return BotChannel.delete().where(
            (BotChannel.id == channel_id) & (BotChannel.tenant_id == tenant_id)
        ).execute()


class BotConversationMapService(CommonService):
    model = BotConversationMap

    @classmethod
    @DB.connection_context()
    def find(
        cls,
        channel_type: str,
        account_id: str,
        conversation_key: str,
    ) -> BotConversationMap | None:
        try:
            return BotConversationMap.select().where(
                (BotConversationMap.channel_type == channel_type)
                & (BotConversationMap.account_id == account_id)
                & (BotConversationMap.conversation_key == conversation_key)
            ).get()
        except DoesNotExist:
            return None

    @classmethod
    @DB.connection_context()
    def upsert(
        cls,
        *,
        channel_type: str,
        account_id: str,
        conversation_key: str,
        agent_session_id: str,
        im_user_id: str | None = None,
        im_user_name: str | None = None,
    ) -> BotConversationMap:
        existing = cls.find(channel_type, account_id, conversation_key)
        now = current_timestamp()
        if existing:
            BotConversationMap.update(
                agent_session_id=agent_session_id,
                im_user_id=im_user_id or existing.im_user_id,
                im_user_name=im_user_name or existing.im_user_name,
                last_activity_ms=now,
            ).where(BotConversationMap.id == existing.id).execute()
            return cls.find(channel_type, account_id, conversation_key)  # type: ignore[return-value]

        return BotConversationMap.create(
            id=get_uuid(),
            channel_type=channel_type,
            account_id=account_id,
            conversation_key=conversation_key,
            agent_session_id=agent_session_id,
            im_user_id=im_user_id,
            im_user_name=im_user_name,
            last_activity_ms=now,
        )

    @classmethod
    @DB.connection_context()
    def touch(cls, mapping_id: str) -> None:
        BotConversationMap.update(
            last_activity_ms=current_timestamp(),
        ).where(BotConversationMap.id == mapping_id).execute()

    @classmethod
    @DB.connection_context()
    def list_by_account(
        cls,
        channel_type: str,
        account_id: str,
        limit: int = 100,
    ) -> list[BotConversationMap]:
        return list(
            BotConversationMap.select().where(
                (BotConversationMap.channel_type == channel_type)
                & (BotConversationMap.account_id == account_id)
            ).order_by(BotConversationMap.last_activity_ms.desc()).limit(limit)
        )

    @classmethod
    @DB.connection_context()
    def delete(cls, mapping_id: str) -> int:
        return BotConversationMap.delete().where(
            BotConversationMap.id == mapping_id
        ).execute()
