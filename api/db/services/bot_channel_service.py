"""IM 机器人渠道与会话映射服务（Phase 2.2）。"""

from __future__ import annotations

from typing import Any

from peewee import DoesNotExist

from api.db.db_models import DB, BotChannel, BotConversationMap
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


class BotChannelService(CommonService):
    model = BotChannel

    # ────────────── CRUD by tenant/admin ──────────────

    @classmethod
    @DB.connection_context()
    def list_by_tenant(cls, tenant_id: str) -> list[BotChannel]:
        return list(
            BotChannel.select().where(BotChannel.tenant_id == tenant_id)
            .order_by(BotChannel.create_time.desc())
        )

    @classmethod
    @DB.connection_context()
    def find(cls, channel_type: str, account_id: str) -> BotChannel | None:
        try:
            return BotChannel.select().where(
                (BotChannel.channel_type == channel_type)
                & (BotChannel.account_id == account_id)
            ).get()
        except DoesNotExist:
            return None

    @classmethod
    @DB.connection_context()
    def get_by_id_for_tenant(cls, channel_id: str, tenant_id: str) -> BotChannel | None:
        try:
            return BotChannel.select().where(
                (BotChannel.id == channel_id) & (BotChannel.tenant_id == tenant_id)
            ).get()
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
        return BotChannel.create(
            id=get_uuid(),
            tenant_id=tenant_id,
            channel_type=channel_type,
            account_id=account_id,
            name=name,
            config_json=config_json or {},
            default_kb_ids=list(default_kb_ids or []),
            default_agent_template_id=default_agent_template_id,
            default_model_config_json=default_model_config_json,
            default_system_prompt=default_system_prompt or "",
            session_scope=session_scope,
            enabled=1 if enabled else 0,
        )

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
