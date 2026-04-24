"""把 RAGFlow TenantLLM 配置翻译成 Claude Agent SDK 需要的 ModelConfig。

优先级：
  1. session.model_config_json 含 `{llm_name, factory}` → 从 TenantLLM 解析
  2. session.model_config_json 含 `{model, auth_token, base_url}` → 直接用（兼容老会话）
  3. 环境变量 `AGENT_V2_DEEPSEEK_KEY` / `AGENT_V2_ANTHROPIC_KEY` 兜底

目前支持的 provider（按 Anthropic-兼容端点映射）：
  - Anthropic: 直接用官方
  - DeepSeek: 走 https://api.deepseek.com/anthropic
    - 默认 model=deepseek-chat；可通过 AGENT_V2_DEEPSEEK_MODEL 指定：
      - `deepseek-v4-flash`（1M context，upstream v0.25.0 新增）
      - `deepseek-v4-pro`（1M context reasoner 升级版）
  - 其他「OpenAI-API-Compatible / VLLM / Ollama」类：尝试 <base>/anthropic，不保证全兼容
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from api.db.services.tenant_llm_service import TenantLLMService
from common.constants import LLMType

from .runner import ModelConfig

logger = logging.getLogger("ragflow.agent_v2.model_resolver")


# Provider → (base_url builder, label)
_PROVIDER_ENDPOINTS = {
    "anthropic": None,  # 官方，不需要 base_url
    "deepseek": "https://api.deepseek.com/anthropic",
}


@dataclass
class ResolvedModel:
    """解析后的模型配置，给 Runner 用。"""

    config: ModelConfig
    source: str  # "tenant_llm" / "inline" / "env_fallback"
    display_name: str


def resolve_model(conf: dict | None, tenant_id: str) -> ResolvedModel:
    """统一入口：按优先级解析 ModelConfig。"""
    conf = conf or {}

    # 1. {llm_name, factory} — 从 TenantLLM 拿 key + base_url
    llm_name = conf.get("llm_name") or conf.get("model")
    factory = conf.get("factory")
    if llm_name and factory:
        full_name = f"{llm_name}@{factory}"
        llm_config = TenantLLMService.get_api_key(
            tenant_id, full_name, LLMType.CHAT
        )
        if llm_config:
            # `TenantLLMService.get_api_key` 返回的是 peewee `TenantLLM` 模型实例
            # （不是 dict）——直接用属性访问。历史上此处误用 ``.get(...)`` 导致
            # peewee 以 ``"api_key"`` 为列过滤发起新查询并抛 TenantLLMDoesNotExist，
            # 整条 TenantLLM 分支都走不通；M1.5 评测走的是 env-var fallback 因此没暴露。
            api_key = getattr(llm_config, "api_key", None)
            api_base = (getattr(llm_config, "api_base", None) or "").rstrip("/")
            provider_key = factory.lower()

            # 按 provider 路由
            if provider_key == "anthropic":
                base_url = api_base or None  # 自定义 base 或官方
            elif provider_key == "deepseek":
                # DeepSeek 兼容 Anthropic 协议，但要走 /anthropic 子路径
                if not api_base:
                    base_url = _PROVIDER_ENDPOINTS["deepseek"]
                elif api_base.endswith("/anthropic"):
                    base_url = api_base
                else:
                    # 用户可能填了 https://api.deepseek.com 或 https://api.deepseek.com/v1
                    base = api_base.removesuffix("/v1")
                    base_url = f"{base}/anthropic"
            else:
                # 其他 provider：尝试 /anthropic 兼容端点
                base_url = (
                    f"{api_base}/anthropic"
                    if api_base and not api_base.endswith("/anthropic")
                    else (api_base or None)
                )

            return ResolvedModel(
                config=ModelConfig(
                    model=llm_name,
                    base_url=base_url,
                    auth_token=api_key,
                ),
                source="tenant_llm",
                display_name=f"{factory} / {llm_name}",
            )
        else:
            logger.warning(
                "resolve_model: TenantLLM lookup miss for tenant=%s model=%s",
                tenant_id,
                full_name,
            )

    # 2. Inline explicit config
    if conf.get("model"):
        return ResolvedModel(
            config=ModelConfig(
                model=conf["model"],
                base_url=conf.get("base_url"),
                auth_token=conf.get("auth_token"),
            ),
            source="inline",
            display_name=f"inline / {conf['model']}",
        )

    # 3. env var fallback（老路径，保留兼容）
    key = os.environ.get("AGENT_V2_DEEPSEEK_KEY") or os.environ.get(
        "DEEPSEEK_API_KEY"
    )
    if key:
        # 默认 deepseek-chat；可通过 AGENT_V2_DEEPSEEK_MODEL 切换到 V4：
        #   - deepseek-v4-flash   — 1M context, tool_use 友好（upstream v0.25.0）
        #   - deepseek-v4-pro     — 1M context, reasoner 升级版
        model = os.environ.get("AGENT_V2_DEEPSEEK_MODEL", "deepseek-chat")
        return ResolvedModel(
            config=ModelConfig(
                model=model,
                base_url="https://api.deepseek.com/anthropic",
                auth_token=key,
            ),
            source="env_fallback",
            display_name=f"env / {model}",
        )
    key = os.environ.get("AGENT_V2_ANTHROPIC_KEY") or os.environ.get(
        "ANTHROPIC_API_KEY"
    )
    if key:
        return ResolvedModel(
            config=ModelConfig(model="claude-sonnet-4-5", auth_token=key),
            source="env_fallback",
            display_name="env / claude-sonnet-4-5",
        )

    raise ValueError(
        "No model configured for this agent session. Either:\n"
        "  1. Set model_config = {llm_name, factory} pointing to a "
        "configured TenantLLM, or\n"
        "  2. Set AGENT_V2_DEEPSEEK_KEY / AGENT_V2_ANTHROPIC_KEY env var."
    )


def list_available_chat_models(tenant_id: str) -> list[dict]:
    """枚举当前 tenant 下所有可用的 Chat 模型，供前端下拉选择。

    只返回我们当前 Agent v2 能跑的 provider（Anthropic / DeepSeek / 兼容类）。
    """
    from api.db.db_models import TenantLLM

    rows = (
        TenantLLM.select()
        .where(
            (TenantLLM.tenant_id == tenant_id)
            & (TenantLLM.model_type.in_([LLMType.CHAT.value, "chat"]))
            & (TenantLLM.status == "1")
            & (TenantLLM.api_key.is_null(False))
        )
        .dicts()
    )

    supported_factories = {
        "Anthropic",
        "DeepSeek",
        "OpenAI-API-Compatible",
        "VLLM",
        "Ollama",
    }

    out = []
    for r in rows:
        factory = r.get("llm_factory") or ""
        llm_name = r.get("llm_name") or ""
        if not llm_name:
            continue
        is_supported = factory in supported_factories
        out.append(
            {
                "llm_name": llm_name,
                "factory": factory,
                "display_name": f"{factory} / {llm_name}",
                "api_base": r.get("api_base") or "",
                "supported": is_supported,
                "note": (
                    None
                    if is_supported
                    else "Provider 可能不支持 Anthropic-compatible 协议，使用前请验证"
                ),
            }
        )
    # 优先展示受支持的 provider
    out.sort(key=lambda x: (0 if x["supported"] else 1, x["factory"], x["llm_name"]))
    return out
