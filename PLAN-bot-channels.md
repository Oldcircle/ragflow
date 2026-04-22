# P2.2 — IM 机器人渠道（飞书优先）

> 企业员工的内部咨询流量主要在飞书/钉钉/企微。本阶段落地飞书机器人适配器，为钉钉/企微预留扩展点。复用 `vendor/openclaw/extensions/feishu/` 的 adapter 模式，融合 RAGFlow 已有 webhook 基建。

---

## 一、现状诊断

**RAGFlow 已具备**：
- Webhook 基建（`api/apps/sdk/agents.py:156-641`）：支持 JWT / Basic / Token / 无鉴权；IP 白名单；速率限制；请求体大小限制
- Public 完成端点（`POST /api/v1/chatbots/<dialog_id>/completions`）基于 `APIToken.beta` 鉴权，可 SSE 流式
- `APIToken` 模型支持 tenant-scoped / dialog-scoped 令牌

**RAGFlow 缺失**：
- 飞书/钉钉/企微的 **inbound 适配层**：签名验证、URL challenge、消息解析、群/私聊/话题分发
- **IM 用户 → RAGFlow 用户** 的映射和会话持久化（每个飞书用户要有持久 Agent v2 session，不能每条消息新建）
- **outbound 发送**：飞书消息 API 封装、tenant_access_token 缓存与刷新

**openclaw 参考架构**（`extensions/feishu/src/`）：
- `monitor.transport.ts`：webhook HTTP 服务；签名 **在 JSON 解析前** 验证（安全边界）
- `conversation-id.ts`：compound ID `feishu:<chat_id>[:topic:<root>][:sender:<open_id>]` 作为路由键
- `monitor.message-handler.ts`：每个 `(account_id, chat_id)` 的消息走顺序队列；dedup by `message_id`
- `reply-dispatcher.ts`：长文本切片 + 媒体 + 结构化卡片；typing indicator
- `accounts.ts`：`appId` / `appSecret` / `encryptKey` / `verificationToken` 可来自 env / SecretRef / 显式

---

## 二、设计总览

**架构图**：
```
┌─────────────────────────────────────────────────────────────────┐
│  飞书开放平台                                                   │
│  ① 用户在飞书发消息  ② URL verification / event push            │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTPS POST /v1/bot/feishu/<account>/events
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  api/apps/bot_app.py                                            │
│    ├─ ③ HMAC(timestamp+nonce+encryptKey+body) 验签              │
│    ├─ ④ URL challenge 路径直接回 {challenge}                    │
│    ├─ ⑤ 消息 dedup by message_id (Redis set, TTL 10min)         │
│    └─ ⑥ 入队（每 account+chat 一个顺序 asyncio.Queue）          │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  api/bot_channels/feishu/handler.py                             │
│    ├─ ⑦ parse_event → FeishuMessageContext                      │
│    ├─ ⑧ resolve_conversation_key (scope 决定分组粒度)           │
│    ├─ ⑨ upsert bot_conversation_map → agent_v2_session_id       │
│    ├─ ⑩ 调用 AgentRunner.run(prompt=正文) 流式拉回复             │
│    └─ ⑪ 把首个大块文本 + 引用脚注打包成卡片发出                  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  飞书 API / Lark Open                                          │
│    ├─ POST /open-apis/im/v1/messages (receive_id, content, ...) │
│    └─ reply_to_message_id 支持话题线程                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、数据模型

### 3.1 `bot_channel`

```sql
CREATE TABLE bot_channel (
  id           VARCHAR(32) PRIMARY KEY,
  tenant_id    VARCHAR(32) NOT NULL,            -- 哪个 tenant 的
  channel_type VARCHAR(16) NOT NULL,            -- "feishu" | "dingtalk" | "wecom"
  account_id   VARCHAR(64) NOT NULL,            -- 该平台下的机器人实例 ID（可自定义）
  name         VARCHAR(128) NOT NULL,
  config_json  JSON NOT NULL,                   -- 见下方 shape
  default_agent_template_id VARCHAR(32) NULL,   -- 新会话创建时用哪个模板
  default_kb_ids JSON NOT NULL DEFAULT '[]',    -- 默认绑定的知识库
  session_scope VARCHAR(32) NOT NULL DEFAULT 'group_sender',
                                                  -- dm | group | group_sender | group_topic | group_topic_sender
  enabled      TINYINT NOT NULL DEFAULT 1,
  create_time  BIGINT NOT NULL,
  update_time  BIGINT NOT NULL,
  UNIQUE KEY uk_type_account (channel_type, account_id),
  INDEX idx_tenant (tenant_id)
);
```

`config_json` 示例（飞书）：
```json
{
  "app_id": "cli_abc123",
  "app_secret_encrypted": "enc:...",   // 用 tenant 密钥加密存储，不明文
  "encrypt_key_encrypted": "enc:...",
  "verification_token_encrypted": "enc:...",
  "api_base": "https://open.feishu.cn"
}
```

### 3.2 `bot_conversation_map`

把 IM 端的会话标识映射到 Agent v2 session，保证**同一用户连续对话在同一 session 里**（不是每条消息新建）。

```sql
CREATE TABLE bot_conversation_map (
  id                 VARCHAR(32) PRIMARY KEY,
  channel_type       VARCHAR(16) NOT NULL,
  account_id         VARCHAR(64) NOT NULL,
  conversation_key   VARCHAR(255) NOT NULL,       -- "feishu:oc_abc:sender:ou_xyz" 等
  agent_session_id   VARCHAR(32) NOT NULL,        -- 指向 agent_v2_session.id
  im_user_id         VARCHAR(128) NULL,           -- open_id / userid
  im_user_name       VARCHAR(128) NULL,           -- 昵称快照
  last_activity_ms   BIGINT NOT NULL,
  create_time        BIGINT NOT NULL,
  UNIQUE KEY uk_conv (channel_type, account_id, conversation_key),
  INDEX idx_session (agent_session_id),
  INDEX idx_last_activity (last_activity_ms)
);
```

**session 生命周期**：首次收到消息 → 创建 agent session（以 `bot_channel.default_agent_template_id` / `default_kb_ids` 为初始配置）→ 之后同 `conversation_key` 的消息都走这个 session。管理员可以在前端手动重置 / 归档。

### 3.3 `bot_message_dedup`（可选，也可以用 Redis）

```sql
CREATE TABLE bot_message_dedup (
  message_id    VARCHAR(128) PRIMARY KEY,
  channel_type  VARCHAR(16) NOT NULL,
  processed_at  BIGINT NOT NULL,
  INDEX idx_processed_at (processed_at)
);
```

或者更轻量：Redis `SETEX bot:dedup:feishu:<mid> 600 1`（10 min TTL，飞书重试窗口一般 5 分钟）。

---

## 四、目录结构

```
api/bot_channels/
├── __init__.py
├── base.py                    # BotChannelAdapter 协议、事件类型、错误类型
├── registry.py                # 按 channel_type 分发到具体 adapter
├── feishu/
│   ├── __init__.py
│   ├── signature.py           # HMAC-SHA256 验签
│   ├── event_types.py         # FeishuEvent / FeishuMessageContext dataclass
│   ├── parser.py              # 原始 JSON → FeishuMessageContext
│   ├── conversation.py        # build_conversation_key
│   ├── handler.py             # 主处理管道（dedup → route → agent → reply）
│   ├── sender.py              # tenant_access_token 缓存、send_message API
│   └── crypto.py              # SecretRef 加解密（config_json 字段级）
└── dingtalk/                  # 占位，P2.5 再实现
    └── __init__.py
```

### 4.1 `base.py` 的 Protocol

```python
@dataclass
class InboundMessage:
    channel_type: str
    account_id: str
    conversation_key: str
    im_user_id: str
    im_user_name: str | None
    text: str                   # 正文
    quoted: str | None          # 引用上文（话题父消息）
    original_message_id: str    # 用于 reply_to
    at_bot: bool
    raw: dict                   # 原始 payload（留档）


@dataclass
class OutboundReply:
    text: str
    citations: list[dict] | None = None   # [{index, doc_name, chunk_id}]
    reply_to: str | None = None            # original_message_id
    mentions: list[str] | None = None      # 要 @ 的用户 im_user_id


class BotChannelAdapter(Protocol):
    def verify_signature(self, headers: dict, raw_body: bytes, config: dict) -> bool: ...
    def try_handle_url_challenge(self, payload: dict) -> dict | None: ...
    def parse_message(self, payload: dict, config: dict) -> InboundMessage | None: ...
    async def send(self, reply: OutboundReply, inbound: InboundMessage, config: dict) -> None: ...
```

所有三个 IM 渠道在未来都实现这个 Protocol，复用 handler 里的路由/会话/Agent 调用逻辑。

---

## 五、HTTP 入口

### 5.1 `api/apps/bot_app.py`

```python
bp = Blueprint("bot_channel", __name__, url_prefix="/v1/bot")

@bp.post("/<channel_type>/<account_id>/events")
async def receive_event(channel_type: str, account_id: str):
    raw_body = await request.get_data()
    
    # 1. 路由到适配器
    adapter = registry.get(channel_type)
    if adapter is None:
        return jsonify({"code": 404, "message": "unsupported channel"}), 404
    
    # 2. 查 bot_channel 配置（带解密）
    bc = BotChannelService.get(channel_type, account_id)
    if bc is None or not bc.enabled:
        return jsonify({"code": 404, "message": "channel not configured"}), 404
    config = decrypt_config(bc.config_json, bc.tenant_id)
    
    # 3. 验签（在 JSON parse 前；签名用 raw_body）
    if not adapter.verify_signature(request.headers, raw_body, config):
        AuditLogService.log(user_id=None, tenant_id=bc.tenant_id,
            action="bot.receive", resource_type="bot_channel",
            resource_id=bc.id, result="deny", reason="bad_signature",
            request=request)
        return Response(status=401)
    
    # 4. 解析 JSON
    payload = json.loads(raw_body)
    
    # 5. URL challenge 直接回
    challenge = adapter.try_handle_url_challenge(payload)
    if challenge is not None:
        return jsonify(challenge)
    
    # 6. dedup
    msg_id = payload.get("header", {}).get("event_id")  # 飞书字段
    if not try_mark_processed(channel_type, msg_id):
        return Response(status=200)  # 重试忽略
    
    # 7. 解析消息
    inbound = adapter.parse_message(payload, config)
    if inbound is None:
        return Response(status=200)  # 非消息事件、bot 自己发的等
    
    # 8. 入顺序队列（不阻塞 webhook 响应；飞书要求 3s 内 200）
    asyncio.create_task(handle_inbound(bc, inbound))
    return Response(status=200)
```

### 5.2 `handle_inbound(bc, inbound)` — 核心分发

```python
async def handle_inbound(bc: BotChannel, inbound: InboundMessage) -> None:
    try:
        # 1. 找到/创建 agent session
        mapping = BotConversationMapService.find_or_create(
            channel_type=bc.channel_type,
            account_id=bc.account_id,
            conversation_key=inbound.conversation_key,
            bot_channel=bc,
        )
        
        # 2. 运行 Agent
        runner = AgentRunner(
            tenant_id=bc.tenant_id,
            kb_ids=list(bc.default_kb_ids),
            system_prompt=SessionService.get(mapping.agent_session_id).system_prompt,
            model=resolve_model_config(mapping.agent_session_id),
            user_id=f"bot:{bc.channel_type}:{inbound.im_user_id}",  # 虚拟用户
            max_turns=...,
            max_budget_usd=...,
        )
        
        # 3. 拉出最终回复
        final_text, citations = await collect_final_reply(runner.run(inbound.text))
        
        # 4. 格式化引用脚注并回传
        adapter = registry.get(bc.channel_type)
        await adapter.send(
            OutboundReply(
                text=format_with_citations(final_text, citations),
                citations=citations,
                reply_to=inbound.original_message_id,
                mentions=[inbound.im_user_id] if inbound.at_bot else None,
            ),
            inbound=inbound,
            config=decrypt_config(bc.config_json, bc.tenant_id),
        )
        
        # 5. 审计
        AuditLogService.log(
            user_id=f"bot:{bc.channel_type}:{inbound.im_user_id}",
            tenant_id=bc.tenant_id,
            action="bot.reply",
            resource_type="agent_v2_session",
            resource_id=mapping.agent_session_id,
            result="allow",
        )
    except Exception as e:
        logger.exception("bot handler failed: %s", e)
        # 飞书已 200 了，这里尽量发一个"抱歉出错"消息
        await try_send_fallback_error(bc, inbound, str(e))
```

### 5.3 `verify_signature`（飞书）

```python
def verify_signature(headers, raw_body, config) -> bool:
    encrypt_key = config["encrypt_key"]
    ts = headers.get("X-Lark-Request-Timestamp", "")
    nonce = headers.get("X-Lark-Request-Nonce", "")
    sig = headers.get("X-Lark-Signature", "")
    if not (encrypt_key and ts and nonce and sig):
        return False
    payload = f"{ts}{nonce}{encrypt_key}{raw_body.decode('utf-8', 'ignore')}".encode()
    computed = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(computed, sig)
```

注意：一定要用 `hmac.compare_digest`（timing-safe），不能用 `==`。

### 5.4 `send_message`（飞书）

```python
async def send(self, reply: OutboundReply, inbound: InboundMessage, config: dict) -> None:
    token = await self._get_tenant_access_token(config)
    # 长文切块（飞书单条 text 上限 30KB，留余地用 10KB 切）
    chunks = chunk_text(reply.text, limit=10_000)
    first_body = {
        "receive_id_type": "chat_id",
        "msg_type": "text",
        "content": json.dumps({"text": chunks[0]}),
    }
    await httpx.post(
        f"{config['api_base']}/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {token}"},
        json=first_body,
    )
    for c in chunks[1:]:
        # 续发，不再 reply_to
        ...
```

**`tenant_access_token` 缓存**：过期前 5 分钟刷新；失败重试 3 次指数退避。

---

## 六、CRUD API（管理员侧）

```
POST   /v1/bot/<channel_type>/channel    {name, account_id, config, default_kb_ids, ...}
GET    /v1/bot/channel                    → list 所有启用的
GET    /v1/bot/<channel_type>/channel/<account_id>
PUT    /v1/bot/<channel_type>/channel/<account_id>
DELETE /v1/bot/<channel_type>/channel/<account_id>

POST   /v1/bot/<channel_type>/channel/<account_id>/test
                                          → 发个假消息验证配置正确、token 能拿到

GET    /v1/bot/<channel_type>/channel/<account_id>/conversation
                                          → 该渠道活跃会话列表
DELETE /v1/bot/conversation/<id>          → 归档某 IM 会话（强制重开）
```

权限：仅 tenant OWNER / ADMIN 可操作。

---

## 七、前端

### 7.1 新页面 `/user-setting/bot-channels`

- 列表：已启用的机器人（channel type icon + 名称 + account_id + 启停开关 + 更新时间）
- "添加机器人" 按钮 → 三态选择：飞书 / 钉钉 / 企微（后两者标"敬请期待"置灰）
- 选飞书 → 表单：`name / app_id / app_secret / encrypt_key / verification_token / default_kb_ids[] / session_scope / default_agent_template_id`
- 测试按钮 → `POST .../test`，显示成功/失败
- 下方显示**回调 URL** 供复制："`https://your-domain/v1/bot/feishu/<account_id>/events`"

### 7.2 对话管理 Tab

列出 `bot_conversation_map` 记录，可以点进看 Agent v2 session 的完整对话（复用 agent-chat 页面的消息组件）。

### 7.3 i18n keys

加 `bot.*` namespace：`bot.channels / bot.addChannel / bot.callback / bot.testButton / bot.appId / bot.appSecret / ...`

---

## 八、安全与运维

1. **签名验证在 JSON parse 前**（防御 JSON payload 注入破坏验签）
2. **`app_secret / encrypt_key` 加密存储**：派生 tenant 密钥，AES-GCM 加密字段
3. **速率限制**：webhook 入口每个 `account_id + IP` 60 req/min
4. **请求体上限**：1 MB（飞书事件通常 < 100 KB）
5. **sequential queue**：以 `conversation_key` 为 key，asyncio.Lock 保证同一会话消息顺序
6. **3 秒超时**：webhook 处理必须尽快返回 200，真正工作走 `create_task`
7. **审计**：成功 / 失败都记 `bot.receive` 事件

---

## 九、验收

1. 真飞书 app + 真 KB 完成一次：
   - 群里 @ 机器人问"保障房申请条件"
   - 1 秒内收到 typing 提示（可选）
   - 3-10 秒内收到带 `[1][2]` 脚注的答复
2. 连续第二条消息走同一 agent session（会话连续性）
3. 主动换另一个用户问 → 不同的 `conversation_key` → 独立 session
4. 审计表有 `bot.receive` + `bot.reply` 记录
5. 删除 `bot_channel` 后，对应回调接口返回 404

---

## 十、延伸（Phase 2.5 / 3）

- 钉钉 + 企微适配器（数据模型共用 `bot_channel` / `bot_conversation_map`，只加 adapter 实现）
- 消息去重持久化（非 Redis 场景用 `bot_message_dedup` 表）
- 机器人专用 APIToken：允许在 IM 消息里让用户输入 `/login <otp>` 绑定内部账号
- 富文本卡片（飞书 card 支持按钮、展开折叠、多模态图片）
