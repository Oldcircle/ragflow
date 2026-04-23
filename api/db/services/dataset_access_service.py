"""数据集访问控制服务（Phase 2.1）。

提供 KB 级 owner / admin / contributor / viewer 四角色的判定、授权、撤销。
向后兼容已有的 `kb.permission ∈ {me, team}` 字段：
  - 显式 dataset_access 记录 → 永远以记录为准
  - 没有显式记录 + kb.permission='team' → 同 tenant 用户隐式 VIEWER
  - 没有显式记录 + kb.permission='me' → 仅创建者可见
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from peewee import DoesNotExist

from api.db.db_models import (
    DB,
    DatasetAccess,
    Knowledgebase,
    UserTenant,
)
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid


class DatasetRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


class _RoleRank(IntEnum):
    """角色比较用，整数大小代表权限高低。"""

    VIEWER = 1
    CONTRIBUTOR = 2
    ADMIN = 3
    OWNER = 4


_ROLE_TO_RANK: dict[str, _RoleRank] = {
    DatasetRole.VIEWER.value: _RoleRank.VIEWER,
    DatasetRole.CONTRIBUTOR.value: _RoleRank.CONTRIBUTOR,
    DatasetRole.ADMIN.value: _RoleRank.ADMIN,
    DatasetRole.OWNER.value: _RoleRank.OWNER,
}


def _rank(role: str | DatasetRole | None) -> int:
    if role is None:
        return 0
    if isinstance(role, DatasetRole):
        role = role.value
    return int(_ROLE_TO_RANK.get(role, 0))


class AccessDeniedError(Exception):
    """用户没有所需角色的访问权限。"""

    def __init__(self, message: str = "access denied", *, kb_id: str | None = None,
                 user_id: str | None = None, required: str | None = None,
                 actual: str | None = None):
        super().__init__(message)
        self.kb_id = kb_id
        self.user_id = user_id
        self.required = required
        self.actual = actual


class DatasetAccessService(CommonService):
    """KB 角色查询 + 显式授权管理。"""

    model = DatasetAccess

    # ────────────── 角色判定 ──────────────

    @classmethod
    @DB.connection_context()
    def effective_role(cls, kb_id: str, user_id: str | None) -> DatasetRole | None:
        """返回用户在 KB 上的有效最高角色；没有访问 → None.

        判定顺序（先满足者赢）：
          1. dataset_access 表里的显式记录
          2. kb.created_by == user_id  → OWNER（兜底，覆盖老数据）
          3. kb.permission == 'team' 且 user 在同 tenant → VIEWER
          4. 否则 None
        """
        if not kb_id or not user_id:
            return None

        # 1. 显式记录（命中即返）
        try:
            row = (
                DatasetAccess.select()
                .where((DatasetAccess.kb_id == kb_id) & (DatasetAccess.user_id == user_id))
                .get()
            )
            return DatasetRole(row.role)
        except DoesNotExist:
            pass

        # 2-3. 走 kb 的 permission + tenant 兜底
        try:
            kb = Knowledgebase.select(
                Knowledgebase.created_by, Knowledgebase.tenant_id, Knowledgebase.permission
            ).where(Knowledgebase.id == kb_id).get()
        except DoesNotExist:
            return None

        if (kb.created_by or "") == user_id:
            return DatasetRole.OWNER

        if kb.permission == "team":
            in_tenant = (
                UserTenant.select()
                .where(
                    (UserTenant.tenant_id == kb.tenant_id)
                    & (UserTenant.user_id == user_id)
                    & (UserTenant.status == "1")
                )
                .exists()
            )
            if in_tenant:
                return DatasetRole.VIEWER

        return None

    @classmethod
    def has_at_least(
        cls,
        kb_id: str,
        user_id: str | None,
        min_role: DatasetRole,
    ) -> bool:
        actual = cls.effective_role(kb_id, user_id)
        return _rank(actual) >= _rank(min_role)

    @classmethod
    def require_at_least(
        cls,
        kb_id: str,
        user_id: str | None,
        min_role: DatasetRole,
    ) -> DatasetRole:
        """检查通过返回实际角色；否则抛 AccessDeniedError."""
        actual = cls.effective_role(kb_id, user_id)
        if _rank(actual) < _rank(min_role):
            raise AccessDeniedError(
                message=f"requires {min_role.value} on dataset {kb_id}",
                kb_id=kb_id,
                user_id=user_id,
                required=min_role.value,
                actual=actual.value if actual else None,
            )
        return actual  # type: ignore[return-value]

    @classmethod
    def filter_accessible_kb_ids(
        cls,
        kb_ids: list[str],
        user_id: str | None,
        min_role: DatasetRole = DatasetRole.VIEWER,
    ) -> list[str]:
        """返回 user_id 至少有 min_role 权限的 kb_id 子集（保持原顺序）。"""
        return [kb_id for kb_id in kb_ids if cls.has_at_least(kb_id, user_id, min_role)]

    # ────────────── 成员管理 ──────────────

    @classmethod
    @DB.connection_context()
    def list_members(cls, kb_id: str) -> list[dict]:
        """显式成员 + 兜底成员（KB 创建者隐式 OWNER）。

        每条返回:
          {
            "user_id": str,
            "role": str,
            "granted_by": str | None,
            "create_time": int,
            "implicit": bool,  # True = 不在 dataset_access 表里
          }
        """
        rows = list(DatasetAccess.select().where(DatasetAccess.kb_id == kb_id))
        members = [
            {
                "user_id": r.user_id,
                "role": r.role,
                "granted_by": r.granted_by,
                "create_time": r.create_time,
                "implicit": False,
            }
            for r in rows
        ]
        member_ids = {m["user_id"] for m in members}

        # 兜底创建者
        try:
            kb = Knowledgebase.select(Knowledgebase.created_by).where(
                Knowledgebase.id == kb_id
            ).get()
            if kb.created_by and kb.created_by not in member_ids:
                members.insert(
                    0,
                    {
                        "user_id": kb.created_by,
                        "role": DatasetRole.OWNER.value,
                        "granted_by": None,
                        "create_time": None,
                        "implicit": True,
                    },
                )
        except DoesNotExist:
            pass

        return members

    @classmethod
    @DB.connection_context()
    def grant(
        cls,
        kb_id: str,
        user_id: str,
        role: DatasetRole,
        granted_by: str | None,
    ) -> DatasetAccess:
        """添加或更新成员角色（upsert）。

        不允许把别人设为 OWNER（OWNER 隐式跟 kb.created_by 走，不应被显式授予）；
        如调用方就是想转让 KB，应改 `Knowledgebase.created_by` 后再 grant 旧 owner 为 ADMIN。
        """
        if role == DatasetRole.OWNER:
            raise ValueError("OWNER role cannot be granted explicitly; transfer kb.created_by instead")

        try:
            row = DatasetAccess.select().where(
                (DatasetAccess.kb_id == kb_id) & (DatasetAccess.user_id == user_id)
            ).get()
            row.role = role.value
            row.granted_by = granted_by
            row.save()
            return row
        except DoesNotExist:
            return DatasetAccess.create(
                id=get_uuid(),
                kb_id=kb_id,
                user_id=user_id,
                role=role.value,
                granted_by=granted_by,
            )

    @classmethod
    @DB.connection_context()
    def revoke(cls, kb_id: str, user_id: str) -> int:
        """撤销显式授权；返回删除条数（0 = 没有显式记录可删）。

        注意：撤销显式记录后，用户可能仍然通过 kb.permission='team' 的兜底
        拿到 VIEWER 权限。要彻底拒绝必须把 KB.permission 改为 'me'。
        """
        return DatasetAccess.delete().where(
            (DatasetAccess.kb_id == kb_id) & (DatasetAccess.user_id == user_id)
        ).execute()
