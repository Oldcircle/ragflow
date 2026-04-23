"""Phase 2.5.3 — Agent Definition Schema。

一个 ``AgentDefinition`` 描述一个 Agent（或 subagent）的完整行为契约。
源头是 claude-code-ref ``loadAgentsDir.ts`` 的 Zod schema，但只抄我们场景
用得上的字段——KB 场景不需要 ``mcpServers`` / ``permissionMode`` / plugin
loading / GrowthBook / 颜色与图标元数据。

字段分组
---------

1. **身份**：``name`` / ``version`` / ``description`` / ``when_to_use``
   （后者给父 Agent 在决定要不要派这个子时看）
2. **行为**：``system_prompt`` / ``model`` / ``max_turns`` / ``max_budget_usd``
3. **工具**：``tools`` / ``disallowed_tools``。``tools="*"`` = 父集合的全部
   （对齐 claude-code-ref ``forkSubagent.ts`` 的 ``tools: ['*']`` 语义）
4. **数据范围**：``kb_scope`` ``"inherit"`` 跟父、``"explicit"`` 走
   ``explicit_kb_ids``
5. **Validator**：与 P2.5.1 citation validator 联动
6. **subagent 能力**：``can_spawn_subagents`` + ``allowed_subagent_types``
   控制哪些 Agent 能派 / 能派谁
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

# kind 的语义：
#   - supervisor：面向用户、会接 user message 的 Agent（历史上的「模板」）
#   - subagent：只由 ``spawn_subagent`` 派出、不直接暴露给终端用户
AgentKind = Literal["supervisor", "subagent"]

# system_prompt 可以是静态字符串，或运行时根据上下文生成（比如用 kb_ids 拼）
SystemPromptFn = Callable[[dict], str]


@dataclass(frozen=True)
class ModelRef:
    """Agent 指定模型的轻量引用。

    与 ``AgentRunner.ModelConfig`` 的区别：``ModelConfig`` 带真实 auth_token
    并参与运行；``ModelRef`` 只描述"应该用什么模型"，runtime 组装时再查
    tenant 的 LLM config。
    """

    model: str  # 具体模型名，如 "claude-sonnet-4-5"
    base_url: str | None = None  # None = Anthropic 原生；"https://api.deepseek.com/anthropic" = DeepSeek
    fallback_model: str | None = None


# 特殊值：model="inherit" 表示使用父 Agent 的模型
ModelInherit = "inherit"


@dataclass(frozen=True)
class AgentDefinition:
    """一个 Agent 的完整行为契约。

    内置 definitions 放 ``definitions/built_in/*.py``；运行时通过
    ``registry.get(name)`` 取用。
    """

    # —— 身份 ——
    name: str
    version: str
    description: str
    when_to_use: str  # 父 Agent 决策用；subagent 必填、supervisor 推荐填
    kind: AgentKind = "supervisor"
    icon: str = "🤖"  # 前端模板卡片用（仅 supervisor 关心）
    category: str = "general"  # policy / legal / finance / customer / general

    # —— 行为 ——
    system_prompt: str | SystemPromptFn = ""
    model: ModelRef | Literal["inherit"] = "inherit"
    max_turns: int = 20
    max_budget_usd: float | None = 0.5

    # —— 工具 ——
    # "*" = 父 Agent 的全集（对应 claude-code-ref forkSubagent 的 tools=['*']）
    # []  = 不允许任何工具（纯推理 Agent）
    # [...] = 具体白名单
    tools: list[str] | Literal["*"] = "*"
    disallowed_tools: tuple[str, ...] = ()

    # —— 数据范围 ——
    kb_scope: Literal["inherit", "explicit"] = "inherit"
    explicit_kb_ids: tuple[str, ...] = ()

    # —— 前端提示 ——
    kb_hints: tuple[str, ...] = ()

    # —— Validator（联动 P2.5.1）——
    citation_enforce: Literal["off", "warn", "strict"] = "warn"
    citation_numeric_strict: bool = True

    # —— subagent 能力（联动 P2.3）——
    can_spawn_subagents: bool = False
    allowed_subagent_types: tuple[str, ...] = ()

    # —— 多轮上下文（联动 P2.5.2）——
    history_turn_limit: int = 10

    def resolve_system_prompt(self, ctx: dict | None = None) -> str:
        """Evaluate system_prompt, invoking the callable form if needed."""
        sp = self.system_prompt
        if callable(sp):
            return sp(ctx or {})
        return sp or ""

    def to_dict(self) -> dict:
        """前端可序列化的字典。``system_prompt`` 如果是函数会 eval 一次拿默认值。"""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "kind": self.kind,
            "icon": self.icon,
            "category": self.category,
            "system_prompt": self.resolve_system_prompt(),
            "model": (
                "inherit"
                if self.model == "inherit"
                else {
                    "model": self.model.model,
                    "base_url": self.model.base_url,
                    "fallback_model": self.model.fallback_model,
                }
            ),
            "max_turns": self.max_turns,
            "max_budget_usd": self.max_budget_usd,
            "tools": self.tools,
            "disallowed_tools": list(self.disallowed_tools),
            "kb_scope": self.kb_scope,
            "explicit_kb_ids": list(self.explicit_kb_ids),
            "kb_hints": list(self.kb_hints),
            "citation_enforce": self.citation_enforce,
            "citation_numeric_strict": self.citation_numeric_strict,
            "can_spawn_subagents": self.can_spawn_subagents,
            "allowed_subagent_types": list(self.allowed_subagent_types),
            "history_turn_limit": self.history_turn_limit,
        }


# 运行时展开 tools 白名单的 helper
def resolve_tools(
    defn: AgentDefinition,
    parent_tools: list[str] | None,
) -> list[str] | None:
    """把 ``AgentDefinition.tools``（可能是 "*"）解析成具体的工具名列表。

    - ``tools="*"`` + parent 有白名单 → 回传父白名单的副本
    - ``tools="*"`` + parent 无白名单（全部启用） → 回传 None（表示全部）
    - ``tools=[...]`` → 回传列表（再扣除 disallowed_tools）
    - ``tools=[]`` → 回传空列表（纯推理）
    """
    if defn.tools == "*":
        base: list[str] | None = list(parent_tools) if parent_tools is not None else None
    else:
        base = list(defn.tools)
    if defn.disallowed_tools and base is not None:
        disallow = set(defn.disallowed_tools)
        base = [t for t in base if t not in disallow]
    return base


__all__ = [
    "AgentDefinition",
    "AgentKind",
    "ModelInherit",
    "ModelRef",
    "SystemPromptFn",
    "resolve_tools",
]
