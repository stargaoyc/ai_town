# 领域设计规范

> 本文档定义 aitown 项目的领域语言、限界上下文、分层落点与真相源约定。
>
> 所有业务代码的组织方式（类放哪个包、函数放哪个模块、状态从哪里读写）必须遵循本规范。
>
> 配套文档：[implementation-style.md](implementation-style.md) · [prompt-style.md](prompt-style.md) · [refactor-style.md](refactor-style.md)

---

## 一、领域语言（Ubiquitous Language）

以下术语在代码、文档、Prompt、讨论中**必须保持一致**。禁止在不同模块用不同词指代同一概念。

| 术语 | 英文标识 | 定义 | 真相源 |
|------|----------|------|--------|
| 角色 | Character | 在小镇中持续生活的虚拟人物，拥有档案、状态、记忆、计划 | PG `characters` 表 |
| 小镇 | Town | 角色生活的虚拟世界地理边界 | `configs/world-map.yaml` |
| 场景 | Scene | 小镇中的一个地点节点（家/学校/咖啡店…） | `configs/world-map.yaml` |
| 世界 | World | 小镇的运行时态（时间/天气/场景动态/资源） | Redis `world:state` |
| 世界节拍 | Tick | 世界引擎推进一次的原子单位（含多个 Evolution） | `core/world_engine.py` |
| 行为 | Action | 角色可执行的原子行为（含 precondition + executor） | `actions/registry.py` |
| 计划 | Plan | 角色的长期/短期目标，影响行为决策 | PG `plans` 表 |
| 记忆片段 | MemoryEpisode | 已发生的、可追溯的经历事件（不可变） | PG `memory_episodes` + pgvector |
| 反思 | Reflection | 从多条 MemoryEpisode 归纳出的高层认知 | PG `reflections` 表 |
| 消息 | Message | 用户与角色之间的对话消息 | PG `messages` 表 |
| 会话 | Conversation | 一组连续消息的上下文容器，含滚动摘要 | PG `conversations` 表 |
| 关系 | Relation | 角色与角色/用户之间的关系（亲密度/类型） | PG `relations` 表 |
| 行为记录 | ActionRecord | Action 执行的不可变事实记录 | PG `action_records` 表 |
| 世界事件 | WorldEvent | 世界状态变化的差分事件 | PG `world_events` 表 |
| 世界快照 | WorldSnapshot | 世界状态的完整快照（冷启动恢复） | PG `world_snapshots` 表 |
| 演化 | Evolution | World Tick 中推进某一维度的组件 | `core/evolutions/` |
| 模块 | Module | 可插拔的功能扩展（character/town/schedule…） | `modules/` |

### 术语使用规则

| 规则 | 说明 |
|------|------|
| 禁止同义词混用 | 统一用「角色」不用「NPC」「虚拟人」「Avatar」 |
| 禁止缩写 | 用 `character_id` 不用 `cid`/`char_id`（数据库字段除外） |
| 禁止工程概念外泄到 Prompt | Prompt 中不出现 Action/schema/field 名（见 [prompt-style.md](prompt-style.md)） |
| 新增术语必须先入表 | 新概念先补充到本表，再在代码中使用 |

---

## 二、限界上下文（Bounded Context）

项目按以下限界上下文组织，每个上下文有明确的职责边界与对外接口。

| 上下文 | 包路径 | 职责 | 对外接口 | 禁止 |
|--------|--------|------|----------|------|
| **世界引擎** | `core/world_engine.py` + `core/evolutions/` | 推进世界状态（时间/天气/场景/资源/事件） | `WorldEngine.start()/stop()` | 直接操作角色状态 |
| **角色节拍** | `core/character_tick.py` | 单个角色的「感知→决策→执行→沉淀」闭环 | `CharacterTickEngine.tick_character()` | 跨角色协调 |
| **行为系统** | `actions/` | Action 定义、注册、候选过滤 | `ActionRegistry.get_candidates()` | 在 Action 里直接写 DB/Redis |
| **消息服务** | `messaging/` | 用户与角色的对话处理 | `MessageService.handle_user_message()` | 在消息层做世界推进 |
| **记忆系统** | `memory/` | Episode 沉淀、检索、反思 | `EpisodeService`/`RetrievalService`/`ReflectionService` | 在记忆层做行为决策 |
| **LLM 客户端** | `llm/` | LLM 调用、Prompt 模板管理 | `LLMClient.chat()`/`PromptTemplates.render()` | 在 LLM 层做业务逻辑 |
| **数据访问** | `db/` | PG 模型、Repository、会话管理 | Repository 的 `get`/`insert`/`update` | 在 Repository 里写业务规则 |
| **适配器** | `adapters/` | 外部平台对接（OneBot/Lark） | 平台特定的消息收发 | 在适配器里做业务决策 |
| **成本控制** | `cost_control/` | LLM 预算、熔断 | `BudgetManager`/`CircuitBreaker` | 在成本层修改业务状态 |
| **安全** | `security/` | Prompt 防护、限流 | `PromptGuard`/`RateLimiter` | 在安全层做业务逻辑 |
| **可观测性** | `observability/` | 日志、指标、追踪、Langfuse | `setup_logging()`/metrics 装饰器 | 在观测层修改业务状态 |
| **模块系统** | `modules/` | 可插拔功能扩展 | 模块接口 | 模块直接耦合核心流程 |
| **MCP 客户端** | `mcp/` | MCP 工具调用 | `MCPClient.call_tool()` | 在 MCP 层做业务决策 |
| **调度器** | `scheduler/` | 定时任务（分区预创建等） | `PartitionScheduler` | 在调度器里写业务逻辑 |

### 上下文间通信规则

| 规则 | 说明 |
|------|------|
| 上下文间通过显式接口通信 | 不直接访问对方内部实现 |
| 禁止循环依赖 | `actions/` 不能依赖 `messaging/`，反之亦然 |
| 共享数据通过 Repository | 不绕过 Repository 直接读写 DB |
| 跨上下文事件用广播 | 世界事件广播 `WORLD_EVENT_BROADCAST`，不直接调用角色 Tick |

---

## 三、分层落点

新增代码时，按以下表格决定它应该放在哪一层。**放错层必须重构。**

### 3.1 分层总览

```text
┌──────────────────────────────────────────────────┐
│  API 层 (api/)        HTTP/WebSocket 入口         │
├──────────────────────────────────────────────────┤
│  Service 层 (messaging/ + scheduler/)  业务编排   │
├──────────────────────────────────────────────────┤
│  Core 层 (core/ + actions/ + memory/)  核心域逻辑 │
├──────────────────────────────────────────────────┤
│  Infrastructure 层 (db/ + llm/ + mcp/ + adapters/)│
├──────────────────────────────────────────────────┤
│  Cross-cutting (observability/ + security/ +      │
│                  cost_control/ + auth/)            │
└──────────────────────────────────────────────────┘
```

### 3.2 各层落点清单

| 代码类型 | 落点 | 示例 |
|----------|------|------|
| World Tick 主循环 | `core/world_engine.py` | `WorldEngine` 类 |
| 世界演化组件 | `core/evolutions/` | `WeatherEvolution` |
| 角色 Tick 主流程 | `core/character_tick.py` | `CharacterTickEngine` |
| Action 定义 | `actions/` 按分类分文件 | `actions/life.py`（生活类） |
| Action 注册表 | `actions/registry.py` | `ActionRegistry` |
| Action 基础结构 | `actions/base.py` | `Action`/`DecisionResult`/`ActionResult` |
| 记忆沉淀服务 | `memory/episode_service.py` | `EpisodeService` |
| 记忆检索服务 | `memory/retrieval_service.py` | `RetrievalService` |
| 反思服务 | `memory/reflection_service.py` | `ReflectionService` |
| 消息处理 | `messaging/service.py` | `MessageService` |
| 主动分享 | `messaging/proactive_sharing.py` | `ProactiveSharingService` |
| WebSocket 推送 | `messaging/websocket.py` | `ConnectionManager` |
| LLM 调用 | `llm/client.py` | `LLMClient` |
| Prompt 模板 | `llm/prompts.py` + `configs/prompts/*.yaml` | `PromptTemplates` |
| PG 数据模型 | `db/models/` | `Character`/`Message`/`MemoryEpisode` |
| Repository | `db/repositories/` | `CharacterRepository` |
| 会话管理 | `db/session.py` | `db` 单例 |
| 平台适配器 | `adapters/` | `OneBotAdapter`/`LarkAdapter` |
| 成本控制 | `cost_control/` | `BudgetManager`/`CircuitBreaker` |
| Prompt 防护 | `security/prompt_guard.py` | `PromptGuard` |
| 限流 | `security/rate_limiter.py` | `RateLimiter` |
| 日志配置 | `observability/logging.py` | `setup_logging()` |
| 指标定义 | `observability/metrics.py` | `WORLD_TICK_DURATION` |
| 追踪配置 | `observability/tracing.py` | `setup_tracing()` |
| Langfuse 集成 | `observability/langfuse_*.py` | Langfuse 包装器 |
| 可插拔模块 | `modules/` | `CharacterModule`/`TownModule` |
| MCP 客户端 | `mcp/client.py` | `MCPClient` |
| MCP 服务器 | `packages/mcp-servers/` | 独立进程 |
| 定时任务 | `scheduler/` | `PartitionScheduler` |
| 配置 | `config.py` + `.env` + `configs/` | `Settings` |

### 3.3 分层依赖规则

| 层 | 可依赖 | 禁止依赖 |
|----|--------|----------|
| API 层 | Service 层、Cross-cutting | 直接访问 Core/Infrastructure |
| Service 层 | Core 层、Infrastructure 层、Cross-cutting | API 层 |
| Core 层 | Infrastructure 层、Cross-cutting | Service 层、API 层 |
| Infrastructure 层 | Cross-cutting | Core 层、Service 层、API 层 |
| Cross-cutting | 无（最底层） | 任何业务层 |

---

## 四、真相源与副作用

### 4.1 真相源矩阵

**同一数据只能有一个真相源。** 其他地方的副本必须明确标注为「镜像」或「缓存」。

| 数据 | 真相源 | 镜像/缓存 | 同步规则 |
|------|--------|-----------|----------|
| 角色实时状态 | Redis `char:{id}:state` | PG `character_states`（周期对齐） | Action 执行后先写 PG，再写 Redis |
| 世界实时状态 | Redis `world:state` | PG `world_snapshots`（每 1000 Tick） | Tick 结束写 Redis，定期快照写 PG |
| 角色档案 | PG `characters` | 无 | 仅管理 API 修改 |
| 行为历史 | PG `action_records` | 无 | 不可变，仅追加 |
| 记忆事实 | PG `memory_episodes` + pgvector | 无 | 不可变，仅追加 |
| 场景静态定义 | `configs/world-map.yaml` | 决策 Prompt（注入时复制） | 修改 YAML 后同步更新 Prompt |
| Prompt 模板 | `configs/prompts/*.yaml` | `PromptTemplates` 内存缓存 | 启动时加载，`reload()` 热更新 |
| 配置 | `.env` + `config.py` | 无 | 启动时读取 |
| 关系 | PG `relations` | 无 | 社交 Action 后更新 |

### 4.2 副作用规则

| 操作 | 允许的副作用位置 | 禁止 |
|------|------------------|------|
| LLM 决策 | 仅返回 `DecisionResult`，无副作用 | 直接修改 Redis/PG |
| Action executor | 计算 `new_state` 字典，返回给执行层 | 直接写 Redis/PG |
| Action 执行层 | 先写 PG 事务，再写 Redis | 在 executor 里直接写 |
| World Tick | 写 Redis `world:state`，定期写 PG 快照 | 在 Evolution 里直接写 Redis |
| 消息处理 | 写 PG `messages`/`conversations` | 在消息层修改角色状态 |
| 记忆检索 | 只读 PG + pgvector | 在检索时写入任何状态 |
| 反思生成 | 写 PG `reflections` | 修改 MemoryEpisode |

### 4.3 状态变更唯一入口

状态只能通过以下途径变更，**任何其他路径都视为违规**：

1. **Action executor**：行为执行的副作用（如 `eat_meal` → `satiety +30`）
2. **World Tick 演化**：全局影响（如天气变冷 → 全员 `stamina -2`）
3. **管理 API**：人工干预（调试/恢复），必须记录审计日志

> LLM 可以选择 Action、生成消息、整理日记、总结记忆；但 LLM **不直接修改** Redis / PG / 文件状态。

---

## 五、常见修改清单

新增/修改功能时，按以下清单确认改动范围是否完整。

### 5.1 新增 Action

| 步骤 | 文件 | 说明 |
|------|------|------|
| 1 | `actions/{category}.py` | 定义 Action（含 precondition + executor） |
| 2 | `actions/registry.py` | 注册到 `ActionRegistry` |
| 3 | `configs/world-map.yaml` | 若需新场景，补充场景定义 |
| 4 | `configs/prompts/decision.yaml` | 若 Action 有特殊约束，补充到 Prompt |
| 5 | `tests/test_actions_*.py` | 补充测试 |

### 5.2 新增角色

| 步骤 | 文件 | 说明 |
|------|------|------|
| 1 | `configs/characters/{name}.yaml` | 角色卡配置 |
| 2 | `modules/character/importer.py` | 确认导入逻辑支持 |
| 3 | `configs/world-map.yaml` | 若角色有专属场景，补充 |
| 4 | `db/migrations/` | 若需新字段，生成迁移 |

### 5.3 新增场景

| 步骤 | 文件 | 说明 |
|------|------|------|
| 1 | `configs/world-map.yaml` | 场景定义 + 连通矩阵 |
| 2 | `configs/prompts/decision.yaml` | 同步世界地图描述 |
| 3 | `actions/move.py` | 移动 Action 自动生成（若用 `register_move_actions`） |
| 4 | `docs/town-design.md` | 同步场景清单 |

### 5.4 新增 Evolution

| 步骤 | 文件 | 说明 |
|------|------|------|
| 1 | `core/evolutions/{name}.py` | 实现 `Evolution` 接口（含 `precondition` + `evolve`） |
| 2 | `core/evolutions/__init__.py` | 注册到 `default_evolutions()` |
| 3 | `db/models/` | 若需新状态字段，补充模型 + 迁移 |
| 4 | `observability/metrics.py` | 补充指标 |
| 5 | `tests/` | 补充测试 |

### 5.5 新增记忆类型

| 步骤 | 文件 | 说明 |
|------|------|------|
| 1 | `db/models/memory_episode.py` | 若需新字段，补充模型 + 迁移 |
| 2 | `memory/episode_service.py` | 沉淀逻辑 |
| 3 | `memory/retrieval_service.py` | 检索逻辑 |
| 4 | `configs/prompts/reflection.yaml` | 若影响反思，补充 |

### 5.6 新增消息平台

| 步骤 | 文件 | 说明 |
|------|------|------|
| 1 | `adapters/{platform}.py` | 实现 Adapter |
| 2 | `messaging/service.py` | 接入消息处理 |
| 3 | `config.py` | 补充配置项 |
| 4 | `.env.example` | 补充配置示例 |

---

## 六、禁止事项

以下行为在 review 时必须被拦截，无例外。

### 6.1 状态相关禁止

| 禁止 | 原因 |
|------|------|
| LLM 直接修改 Redis/PG | LLM 输出不可信，必须经 Action executor 校验 |
| 在 PG 维护"当前状态" | Redis 是实时状态真相源，PG 仅镜像 |
| 在内存里缓存状态跨请求 | 破坏单一真相源，多实例不一致 |
| Action executor 直接写 Redis | 必须返回 `new_state`，由执行层统一写入 |

### 6.2 架构相关禁止

| 禁止 | 原因 |
|------|------|
| 跨上下文直接访问内部实现 | 破坏限界上下文，增加耦合 |
| 循环依赖 | 无法独立测试 |
| 在 Infrastructure 层写业务逻辑 | 层次倒置 |
| 在 Repository 里写业务规则 | Repository 只做数据访问 |
| 在适配器层做业务决策 | 适配器只做协议转换 |

### 6.3 Prompt 相关禁止

| 禁止 | 原因 |
|------|------|
| 在代码里内嵌 Prompt 字符串 | Prompt 必须外置到 `configs/prompts/*.yaml` |
| Prompt 中暴露工程概念 | 不出现 Action/schema/field 名 |
| 在 Prompt 中注入未经校验的用户输入 | Prompt 注入风险 |

### 6.4 可观测性相关禁止

| 禁止 | 原因 |
|------|------|
| 用 `print()` 输出 | 必须用 structlog |
| 用 f-string 拼接日志 | 必须用 `key=value` |
| ERROR 不带 `exc_info=True` | 丢失堆栈信息 |
| 日志中输出敏感信息 | API Key/JWT/密码泄露 |

### 6.5 其他禁止

| 禁止 | 原因 |
|------|------|
| 为单一实现建接口/工厂 | 过度抽象 |
| `except Exception: pass` | 兜底掩盖边界 |
| 同步阻塞 I/O 在 async 函数中 | 阻塞事件循环 |
| 提交不含测试的新功能 | 无法保证正确性 |

---

## 相关文档

| 主题 | 文档 |
|------|------|
| 代码风格规范 | [implementation-style.md](implementation-style.md) |
| Prompt 规范 | [prompt-style.md](prompt-style.md) |
| 重构规则 | [refactor-style.md](refactor-style.md) |
| 角色设计 | [../character-design.md](../character-design.md) |
| 小镇设计 | [../town-design.md](../town-design.md) |
| 世界引擎 | [../world-engine.md](../world-engine.md) |
| Action 系统 | [../action-system.md](../action-system.md) |
| 记忆系统 | [../memory-system.md](../memory-system.md) |
| 架构总览 | [../architecture.md](../architecture.md) |
