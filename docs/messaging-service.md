# 消息服务设计

> 消息服务层负责多平台消息接入、标准化、会话上下文管理、回复生成与主动推送。是用户接入层与世界引擎层之间的桥梁。

---

## 一、设计目标

| 目标 | 说明 |
|------|------|
| 多平台统一 | QQ/飞书/Web 共用一套消息模型与处理流程 |
| 上下文连续 | 跨平台会话上下文持久化，角色"记得"用户 |
| 主动推送 | 角色可基于 `proactiveShareIntent` 主动联系用户 |
| 异步解耦 | 平台收发与 LLM 回复生成异步解耦，避免阻塞 |

---

## 二、多平台接入

### 2.1 平台与协议

| 平台 | 协议 | 实现方式 |
|------|------|----------|
| QQ | OneBot v12 | `aiocqhttp` 异步客户端 |
| 飞书 | Lark OpenAPI | `lark-python` SDK |
| Web | WebSocket | FastAPI WebSocket 端点 |
| API | HTTP | 第三方集成 |

### 2.2 适配器模式

```python
# messaging/adapters/base.py
from abc import ABC, abstractmethod

class PlatformAdapter(ABC):
    platform: str

    @abstractmethod
    async def receive(self) -> AsyncIterator[RawMessage]: ...

    @abstractmethod
    async def send(self, msg: StandardMessage) -> None: ...


# messaging/adapters/qq.py
class QQAdapter(PlatformAdapter):
    platform = "qq"
    def __init__(self, ws_url: str): ...


# messaging/adapters/lark.py
class LarkAdapter(PlatformAdapter):
    platform = "lark"


# messaging/adapters/web.py
class WebAdapter(PlatformAdapter):
    platform = "web"
```

每个适配器负责：
1. 监听平台消息事件；
2. 将平台原生消息格式转换为**标准消息**（`StandardMessage`）；
3. 将标准回复转换回平台格式并发送。

---

## 三、标准消息模型

```python
@dataclass
class StandardMessage:
    id: UUID
    conversation_id: UUID        # 跨平台会话 ID
    platform: str                # qq / lark / web / api
    user_id: str                 # 平台用户标识
    character_id: UUID           # 目标角色
    role: str                    # user / assistant / system / tool
    content: str                 # 文本内容
    attachments: list[Attachment]  # 附件(图片/文件)
    metadata: dict               # 平台特定字段
    created_at: datetime
```

会话 ID 由 `(platform, user_id, character_id)` 唯一确定，确保同一用户与同一角色的对话跨平台延续。

---

## 四、消息处理流程

```text
平台消息
    ↓
适配器接收 (receive)
    ↓
标准化为 StandardMessage
    ↓
权限校验 (白名单)
    ↓
会话上下文加载 (从 messages 表 + Redis 缓存)
    ↓
构造角色上下文 (角色状态 + 世界状态 + 记忆检索)
    ↓
LLM 生成回复 (绑定工具)
    ↓
工具调用循环 (LangGraph)
    ↓
发送回复 (适配器 send)
    ↓
记录对话历史 (写入 messages 表, 单事务)
```

### 4.1 关键步骤

#### 权限校验

```python
def check_permission(user_id: str, character_id: UUID) -> bool:
    """白名单校验"""
    if user_id in ADMIN_WHITELIST:
        return True
    return character_repo.user_has_access(user_id, character_id)
```

#### 会话上下文加载

```python
async def load_conversation_context(conv_id: UUID) -> list[Message]:
    # 1. 优先读 Redis 缓存(最近 20 条)
    cached = await redis.lrange(f"conv:{conv_id}", 0, 19)
    if cached:
        return [Message.from_json(m) for m in cached]
    # 2. 未命中则从 PG 读取
    messages = await message_repo.recent(conv_id, limit=20)
    # 3. 回填缓存
    await redis.rpush(f"conv:{conv_id}", *[m.to_json() for m in messages])
    await redis.expire(f"conv:{conv_id}", 3600)
    return messages
```

#### 角色上下文构造

LLM 输入包含：

```text
[角色档案] name / personality / backstory
[当前状态] location / energy / mood / current_action
[世界状态] time / weather / scene
[相关记忆] pgvector 检索 Top-K
[近期对话] 最近 N 轮
[可用工具] 已启用模块的工具列表
[用户消息] ...
```

#### LLM 回复生成

使用 LangGraph 的 ReAct 模式：

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(llm, tools=enabled_tools, state_modifier=prompt)
result = await agent.ainvoke({"messages": context_messages})
```

支持多轮工具调用，最终输出 assistant 回复。

---

## 五、主动推送机制

### 5.1 触发条件

角色在 Action 执行后，根据 `proactiveShareIntent` 决定是否主动联系用户：

```python
@dataclass
class ProactiveShareIntent:
    should_share: bool          # 是否推送
    target_user_id: str         # 目标用户
    reason: str                 # 推送理由
    content_hint: str           # 内容提示
```

### 5.2 推送流程

```text
Action 执行完成
    ↓
检查 proactiveShareIntent.should_share = true
    ↓
LLM 生成分享内容 (基于 Action 结果)
    ↓
推送至 Redis Stream (mq:push)
    ↓
消息服务消费 → 适配器发送 → 用户
    ↓
记录到 messages 表 (role=assistant)
```

### 5.3 推送示例

```text
角色在咖啡店学会了一个新的拉花图案
    ↓ should_share=true, target_user_id=alice
LLM 生成: "今天我学会了心形拉花！明天做给你看～"
    ↓
通过 QQ 适配器推送给 alice
```

---

## 六、消息队列（Redis Streams）

### 6.1 Stream 设计

| Stream | 生产者 | 消费者 | 说明 |
|--------|--------|--------|------|
| `mq:incoming` | 适配器 | 消息处理器 | 入站消息 |
| `mq:push` | 角色引擎 | 推送调度器 | 主动推送 |
| `mq:events` | 世界引擎 | 角色引擎 | 世界/角色事件 |

### 6.2 消费组

```text
消费组: msg-workers      # 消息处理器组, 多实例负载均衡
消费组: push-workers     # 推送调度器组
消费组: char-tick-{cid}  # 每角色独立事件消费组
```

### 6.3 可靠投递

- 消费者处理完成后 `XACK` 确认；
- 失败消息进入 Pending 列表，由死信处理器重试或告警；
- 最多重试 3 次，超限进入死信 Stream `mq:dead`。

---

## 七、会话管理

### 7.1 会话 ID 生成

```python
def make_conversation_id(platform: str, user_id: str, character_id: UUID) -> UUID:
    """同一用户与同一角色的对话跨平台延续"""
    key = f"{platform}:{user_id}:{character_id}"
    return uuid.uuid5(NAMESPACE_OID, key)
```

### 7.2 多角色对话

一个用户可与多个角色对话，每个 `(user_id, character_id)` 对应独立会话。用户可在 Dashboard 切换角色。

### 7.3 会话列表

```http
GET /api/v1/conversations?user_id=alice
```

返回该用户与各角色的会话列表，含最近一条消息预览。

---

## 八、附件处理

| 附件类型 | 处理 |
|----------|------|
| 图片 | 上传至对象存储（MinIO/S3），返回 URL；LLM 视觉模型可读取 |
| 文件 | 同上，文本类文件可提取内容注入上下文 |
| 语音 | 转写为文本（Whisper），原文件存对象存储 |

---

## 九、可观测埋点

| Span | 关键属性 |
|------|----------|
| `message.receive` | `platform`, `user_id`, `character_id` |
| `message.process` | `platform`, `session_id`, `response_time_ms` |
| `message.send` | `platform`, `message_id`, `success` |
| `message.push` | `character_id`, `target_user_id`, `reason` |

详见 [可观测性设计](observability.md)。

---

## 十、相关文档

| 主题 | 文档 |
|------|------|
| API 端点 | [api-spec.md](api-spec.md) |
| 世界引擎 | [world-engine.md](world-engine.md) |
| 模块与工具 | [module-system.md](module-system.md) |
| 配置参考 | [config-reference.md](config-reference.md) |
