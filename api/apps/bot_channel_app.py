"""IM 机器人渠道 CRUD（Phase 2.2）。

URL 前缀：``/v1/bot_channel``。

  GET    /v1/bot_channel/list                       — 列出当前 tenant 下所有 channel
  GET    /v1/bot_channel/<channel_id>               — 详情
  POST   /v1/bot_channel                            — 创建
  PUT    /v1/bot_channel/<channel_id>               — 更新
  DELETE /v1/bot_channel/<channel_id>               — 删除
"""

from __future__ import annotations

import logging

from api.apps import current_user, login_required
from api.db.services.bot_channel_service import BotChannelService
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    get_request_json,
    server_error_response,
    validate_request,
)
from common.constants import RetCode

logger = logging.getLogger("ragflow.bot.channel_admin")

_REDACT_KEYS = {"app_secret", "encrypt_key", "verification_token"}


def _channel_dict(bc, *, redact: bool = True) -> dict:
    cfg = dict(bc.config_json or {})
    if redact:
        for k in _REDACT_KEYS:
            if cfg.get(k):
                v = str(cfg[k])
                cfg[k] = (v[:4] + "***" + v[-2:]) if len(v) > 8 else "***"
    return {
        "id": bc.id,
        "tenant_id": bc.tenant_id,
        "channel_type": bc.channel_type,
        "account_id": bc.account_id,
        "name": bc.name,
        "config_json": cfg,
        "default_kb_ids": list(bc.default_kb_ids or []),
        "default_agent_template_id": bc.default_agent_template_id,
        "default_model_config_json": bc.default_model_config_json,
        "default_system_prompt": bc.default_system_prompt,
        "session_scope": bc.session_scope,
        "enabled": bool(bc.enabled),
        "create_time": bc.create_time,
        "update_time": bc.update_time,
    }


@manager.route("/list", methods=["GET"])  # noqa: F821
@login_required
async def list_channels():
    try:
        rows = BotChannelService.list_by_tenant(current_user.id)
        return get_json_result(data={
            "channels": [_channel_dict(r) for r in rows],
        })
    except Exception as e:
        return server_error_response(e)


@manager.route("/<channel_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_channel(channel_id: str):
    try:
        bc = BotChannelService.get_by_id_for_tenant(channel_id, current_user.id)
        if bc is None:
            return get_json_result(code=RetCode.NOT_FOUND, message="not found")
        return get_json_result(data=_channel_dict(bc))
    except Exception as e:
        return server_error_response(e)


@manager.route("", methods=["POST"])  # noqa: F821
@login_required
@validate_request("channel_type", "account_id", "name", "config_json")
async def create_channel():
    try:
        req = await get_request_json()
        bc = BotChannelService.create(
            tenant_id=current_user.id,
            channel_type=req["channel_type"],
            account_id=req["account_id"],
            name=req["name"],
            config_json=req.get("config_json") or {},
            default_kb_ids=req.get("default_kb_ids") or [],
            default_agent_template_id=req.get("default_agent_template_id"),
            default_model_config_json=req.get("default_model_config_json"),
            default_system_prompt=req.get("default_system_prompt") or "",
            session_scope=req.get("session_scope") or "group_sender",
            enabled=req.get("enabled", True),
        )
        return get_json_result(data=_channel_dict(bc))
    except ValueError as e:
        return get_data_error_result(message=str(e))
    except Exception as e:
        return server_error_response(e)


@manager.route("/<channel_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_channel(channel_id: str):
    try:
        req = await get_request_json()
        bc = BotChannelService.get_by_id_for_tenant(channel_id, current_user.id)
        if bc is None:
            return get_json_result(code=RetCode.NOT_FOUND, message="not found")
        rows = BotChannelService.update(channel_id, current_user.id, req)
        if rows == 0:
            return get_data_error_result(message="no fields to update")
        bc = BotChannelService.get_by_id_for_tenant(channel_id, current_user.id)
        return get_json_result(data=_channel_dict(bc) if bc else {})
    except Exception as e:
        return server_error_response(e)


@manager.route("/<channel_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_channel(channel_id: str):
    try:
        deleted = BotChannelService.delete(channel_id, current_user.id)
        return get_json_result(data={"deleted": deleted})
    except Exception as e:
        return server_error_response(e)
