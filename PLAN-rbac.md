# P2.1 — 数据集 RBAC + 审计

> 最小闭环的企业级访问控制。不做字段级 / 密级 / SAML（那是 Phase 3），只做**知识库级三角色**、**关键路径的访问检查**、**审计日志**。

---

## 一、现状诊断（来自 survey）

**当前模型**：
- `tenant ≡ team/organization`；`user_tenant(user_id, tenant_id, role)` 管成员
- `kb.permission` 只有 `"me" | "team"` 两档
- 存在 `KnowledgebaseService.accessible(kb_id, user_id)` 帮手方法但**未被调用在关键路径**

**三个关键漏洞**（这是 Phase 2 必须修的）：

| 漏洞 | 位置 | 风险 |
|---|---|---|
| `async_ask()` 不校验用户对 `kb_ids` 的访问权限 | `api/db/services/dialog_service.py:1405` | 普通用户构造请求可越权查询他人 KB |
| Agent v2 session 创建不校验 `kb_ids` 所有权 | `api/apps/agent_v2_app.py:127-137` | 同上 |
| `rag_retrieve` 工具不重新校验 | `api/agent_v2/tools/rag_retrieve.py:79` | 深度防御缺失 |

**额外缺口**：
- 没有比 `me / team` 更细的 KB 成员角色（不能让 A 只读、B 可写、C 可管）
- 没有访问审计表，管理员看不到"谁在什么时候访问过什么"

---

## 二、设计：三个角色 + 向后兼容

### 2.1 角色模型

```
OWNER       = KB 创建者，隐式最高权限（不可撤销）
ADMIN       = 能改 KB 设置、管理成员、删除 KB
CONTRIBUTOR = 能上传/解析/删除文档，不能管理成员和设置
VIEWER      = 只读：检索、聊天、问答
```

**向后兼容规则**（关键）：
- 保留 `kb.permission` 列不动
- 当 `permission = "me"` → 只 OWNER 能看
- 当 `permission = "team"` → tenant 全员被视为隐式 VIEWER，**除非**在 `dataset_access` 表中有更高角色的显式授权
- `dataset_access` 表里的显式授权永远覆盖 `permission` 默认值

这样已有数据不用迁移，新数据可以细化。

### 2.2 新表

```sql
-- 显式成员角色表
CREATE TABLE dataset_access (
  id        VARCHAR(32) PRIMARY KEY,
  kb_id     VARCHAR(32) NOT NULL,
  user_id   VARCHAR(255) NOT NULL,
  role      VARCHAR(16) NOT NULL,    -- owner | admin | contributor | viewer
  granted_by VARCHAR(255),            -- 谁加的
  create_time  BIGINT NOT NULL,
  update_time  BIGINT NOT NULL,
  UNIQUE KEY uk_kb_user (kb_id, user_id),
  INDEX idx_kb (kb_id),
  INDEX idx_user (user_id)
);

-- 访问审计日志
CREATE TABLE access_audit_log (
  id        VARCHAR(32) PRIMARY KEY,
  user_id   VARCHAR(255),             -- nullable (匿名/机器人请求)
  tenant_id VARCHAR(32) NOT NULL,
  action    VARCHAR(32) NOT NULL,     -- kb.create|kb.read|kb.update|kb.delete|kb.share|kb.retrieve|kb.ingest|...
  resource_type VARCHAR(32) NOT NULL, -- knowledgebase | document | agent_v2_session | bot_channel
  resource_id   VARCHAR(64),
  result    VARCHAR(16) NOT NULL,     -- allow | deny
  reason    VARCHAR(255),              -- deny 时填理由
  metadata  JSON,                      -- 请求参数摘要
  ip        VARCHAR(45),
  user_agent TEXT,
  create_time  BIGINT NOT NULL,
  INDEX idx_user (user_id, create_time),
  INDEX idx_tenant (tenant_id, create_time),
  INDEX idx_resource (resource_type, resource_id, create_time)
);
```

**不改上游表**。所有模型都是新增。

---

## 三、新服务

### 3.1 `api/db/services/dataset_access_service.py`

```python
class DatasetRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


_ROLE_ORDER = {
    DatasetRole.VIEWER: 0,
    DatasetRole.CONTRIBUTOR: 1,
    DatasetRole.ADMIN: 2,
    DatasetRole.OWNER: 3,
}


class DatasetAccessService:
    @classmethod
    def effective_role(cls, kb_id: str, user_id: str) -> DatasetRole | None:
        """返回用户在该 KB 上的最高有效角色；没有任何访问 → None.

        判定顺序:
          1. dataset_access 显式记录
          2. kb.created_by == user_id → OWNER
          3. kb.permission == "team" 且 user 在同 tenant → VIEWER
          4. 否则 None
        """

    @classmethod
    def require_role(cls, kb_id: str, user_id: str, min_role: DatasetRole) -> None:
        """检查失败时抛 AccessDeniedError（同步也记审计日志 deny）."""

    @classmethod
    def list_members(cls, kb_id: str) -> list[DatasetMember]: ...

    @classmethod
    def grant(cls, kb_id: str, user_id: str, role: DatasetRole, granted_by: str) -> None: ...

    @classmethod
    def revoke(cls, kb_id: str, user_id: str, revoked_by: str) -> None: ...
```

### 3.2 `api/db/services/audit_log_service.py`

```python
class AuditLogService:
    @classmethod
    def log(cls, *, user_id: str | None, tenant_id: str, action: str,
            resource_type: str, resource_id: str | None,
            result: Literal["allow", "deny"], reason: str | None = None,
            metadata: dict | None = None,
            request=None) -> None:
        """写一条审计记录。从 flask.request 自动抓 IP / User-Agent."""
```

**所有 deny 必须落 log**（合规基线）；allow 按频率采样，避免淹没日志。

---

## 四、修补三个关键漏洞

### 4.1 `dialog_service.async_ask`

```python
# 在 async_ask 入口（约 line 1400）前插入：
for kb_id in req["kb_ids"]:
    role = DatasetAccessService.effective_role(kb_id, user_id)
    if role is None:
        AuditLogService.log(
            user_id=user_id, tenant_id=..., action="kb.retrieve",
            resource_type="knowledgebase", resource_id=kb_id,
            result="deny", reason="no_access",
        )
        raise AccessDeniedError(f"User has no access to kb {kb_id}")
```

### 4.2 `agent_v2_app.create_session`

```python
# 创建前批量校验
for kb_id in req["kb_ids"]:
    DatasetAccessService.require_role(kb_id, current_user.id, DatasetRole.VIEWER)
# 记一条 allow
```

### 4.3 `rag_retrieve` 工具

```python
# tools/rag_retrieve.py
# 从 ToolContext 拿 user_id（需要先在 ToolContext 里存）
for kb_id in kb_ids:
    if not DatasetAccessService.effective_role(kb_id, ctx.user_id):
        return {"error": f"access_denied: {kb_id}"}  # 工具错误，不中断 agent
```

这是深度防御：即便 session 是老数据有"脏" kb_ids，工具执行时也拦住。

---

## 五、API 端点（新增）

```
GET    /v1/kb/<kb_id>/member            → 列出成员 + 角色
POST   /v1/kb/<kb_id>/member            → {user_email|user_id, role} 添加或更新成员
DELETE /v1/kb/<kb_id>/member/<user_id>  → 撤销成员

GET    /v1/audit?resource_type=...&resource_id=...&start=...&end=...&limit=...
                                          → 分页查审计日志（仅 tenant owner/admin 可查）
```

所有端点加 `@login_required` + 内部做 `require_role(kb_id, caller, ADMIN)` 检查。

---

## 六、前端改动

### 6.1 新 Tab：知识库详情 → 成员

`web/src/pages/dataset/dataset-members/index.tsx`（新文件）

内容：
- 成员列表表格：头像 + 名称 + 邮箱 + 角色下拉 + 移除按钮
- "邀请成员" 按钮 → 弹窗输入邮箱 + 选角色 → 后端查用户 → 加入
- 权限判断：只有 OWNER / ADMIN 能看到这个 Tab；VIEWER / CONTRIBUTOR 进来 403

在 `dataset/sidebar/index.tsx` 的导航数组里加一项 `{ label: t('knowledgeDetails.members'), key: Routes.DataSetMember, ... }`。

### 6.2 路由

`web/src/routes.tsx`：

```ts
DataSetMember = '/dataset-member',

// 在 dataset 子路由数组里加：
{
  path: `${Routes.DatasetBase}${Routes.DataSetMember}/:id`,
  Component: () => import('@/pages/dataset/dataset-members'),
},
```

### 6.3 i18n 新 key

```
knowledgeDetails.members = "成员"
knowledgeDetails.inviteMember = "邀请成员"
knowledgeDetails.role.owner = "所有者"
knowledgeDetails.role.admin = "管理员"
knowledgeDetails.role.contributor = "协作者"
knowledgeDetails.role.viewer = "只读"
knowledgeDetails.roleTipOwner = "创建者。完全控制权限"
knowledgeDetails.roleTipAdmin = "管理成员、设置、删除知识库"
knowledgeDetails.roleTipContributor = "上传 / 解析 / 删除文档"
knowledgeDetails.roleTipViewer = "检索 / 问答 / 引用"
```

---

## 七、验收标准

1. **功能**：
   - 给定用户 A 是 KB-X 的 OWNER，用户 B 不在任何 dataset_access 记录里
   - A 可以邀请 B 为 VIEWER
   - B 之后可以在聊天里选 KB-X 作为知识库，且 `rag_retrieve` 能查
   - A 把 B 的角色降到空（revoke）后，B 再发起带 KB-X 的请求直接 403
2. **审计**：对同一操作，审计表里有对应 allow / deny 记录；tenant admin 能通过 `/v1/audit` 查到
3. **回归**：
   - 原 `kb.permission = "team"` 的 KB 对 tenant 内其他用户仍然可检索（兜底 VIEWER 逻辑）
   - 原 `kb.permission = "me"` 的 KB 对其他用户仍然 deny（除非显式 grant）

---

## 八、实现顺序

1. DB 表 + `DatasetAccessService` + `AuditLogService` 单测
2. 修三个关键漏洞
3. 成员管理 HTTP API
4. 前端成员 Tab
5. 审计查询端点 + 简单前端表格（可以 Phase 2 尾巴做）
