"""Agent v2 文档运营工具（Phase 2.6）。

向 Agent 暴露 KB 业务语义的**写**操作——打标签 / 重命名 / 归档 /
重解析 / 从 URL 入库 / 新建分类桶。**不**引入 bash / 裸 FS 访问。

设计红线（详见 ``PLAN-doc-ops.md``）：

- Supervisor 默认不拿写工具；写工具集中到 ``sub_archivist`` subagent
- 所有写操作必须过 ``@require_kb_write`` 门禁（RBAC + 审计）
- 只在用户**明确指令**时调用（靠 system prompt 自律）
- 破坏性操作走 ``submit_plan`` 审批流程（本 Phase v1 靠 prompt；v2 运行时强制）
"""

from .doc_archive import doc_archive  # noqa: F401
from .doc_create_note import doc_create_note  # noqa: F401
from .doc_list_recent_changes import doc_list_recent_changes  # noqa: F401
from .doc_rename import doc_rename  # noqa: F401
from .doc_reparse import doc_reparse  # noqa: F401
from .doc_tag import doc_tag  # noqa: F401
from .doc_upload_from_url import doc_upload_from_url  # noqa: F401
from .kb_audit import kb_audit  # noqa: F401
from .kb_create import kb_create  # noqa: F401
from .kb_stats import kb_stats  # noqa: F401

__all__ = [
    # 写 — sub_archivist 专用
    "doc_archive",
    "doc_rename",
    "doc_reparse",
    "doc_tag",
    "doc_upload_from_url",
    "kb_create",
    # 写 — 但产出的是 Agent 自己生成的笔记；sub_librarian 专用
    "doc_create_note",
    # 读 — sub_librarian 专用（audit / snapshot / reflection）
    "kb_audit",
    "kb_stats",
    "doc_list_recent_changes",
]
