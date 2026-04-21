"""Agent v2 自定义异常层次。"""


class AgentError(Exception):
    """Agent 运行时错误基类。"""


class ToolError(AgentError):
    """工具执行错误。会被 Runner 捕获并转成 tool_call_end(error=...)。"""


class ContextError(AgentError):
    """工具上下文缺失或无效（如 tenant_id/kb_ids 未注入）。"""


class ModelConfigError(AgentError):
    """模型配置错误（API Key 缺失、provider 不支持等）。"""
