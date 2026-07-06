# 世界引擎设计

> 世界引擎是系统的核心调度器，由两个独立的异步循环组成：**World Tick**（世界状态推进）与 **Character Tick**（角色行为推进），并通过多智能体调度器协调角色间交互。

---

## 一、设计目标

| 目标 | 说明 |
|------|------|
| 世界持续运转 | 世界状态推进不依赖用户消息，用户离线时角色依然生活 |
| 解耦推进节奏 | World Tick 与 Character Tick 各自独立循环，互不阻塞 |
| 可暂停与回放 | 支持暂停/恢复世界推进，支持基于快照的状态回放 |
| 可观测 | 每个 Tick 必须有 Trace 覆盖，便于调试与性能分析 |

---

## 二、World Tick（世界状态推进）

### 2.1 职责

推进与角色无关的全局状态。

### 2.2 子功能与更新频率

| 子功能 | 说明 | 更新频率 |
|--------|------|----------|
| 时间推进 | 虚拟时钟按 Tick 步进（如每 30 秒推进 10 分钟） | 每 Tick |
| 天气系统 | 晴天/多云/阴雨/雪/大风，影响角色行为选择 | 每 60 Tick |
| 场景状态 | 各地点（咖啡店、学校、公园）开放/关闭/拥挤度 | 每 Tick |
| 资源循环 | 城镇资源（食物、能源）动态增减 | 每 Tick |
| 节日系统 | 特殊日期的触发与广播 | 按日期 |

### 2.3 设计约束

- World Tick **不依赖用户消息**——世界在用户不在时依然运转。
- World Tick **不直接修改角色状态**，只修改世界状态（天气、场景、资源、事件）。
- 世界实时态存 Redis，周期性快照存 PG `world_snapshots`（用于回放/调试）。

### 2.4 伪代码

```python
async def world_tick_loop(engine: WorldEngine):
    while engine.running:
        async with engine.tick_lock():
            tick_id = engine.next_tick_id()
            span = start_span("world.tick", tick_id=tick_id)

            # 1. 推进虚拟时钟
            engine.advance_clock(minutes=engine.config.tick_minutes)

            # 2. 更新天气（按频率）
            if tick_id % engine.config.weather_interval == 0:
                engine.update_weather()

            # 3. 更新场景开放/拥挤度
            engine.update_scenes()

            # 4. 资源循环
            engine.update_resources()

            # 5. 节日/事件触发
            engine.check_events()

            # 6. 持久化快照（每 N Tick 一次）
            if tick_id % engine.config.snapshot_interval == 0:
                await engine.persist_snapshot()

            # 7. 广播给订阅的 WebSocket 客户端
            await engine.broadcast_state()

            span.end()

        await asyncio.sleep(engine.config.tick_seconds)
```

### 2.5 世界状态结构（Redis Hash）

```text
world:state
  current_time:    <epoch ms>
  weather:         sunny|cloudy|rainy|snowy|windy
  temperature:     <int>
  locations:       { cafe: {open:1, crowdedness:23, visitors:[...]},
                     school: {...}, park: {...} }
  resources:       { food: 87, energy: 92 }
  active_events:   [ {id, name, description}, ... ]
  tick_id:         <bigint>
```

详细字段定义见 [数据模型设计](data-model.md#world_snapshots)。

---

## 三、Character Tick（角色行为推进）

### 3.1 职责

每个角色独立执行"感知→决策→执行→沉淀"闭环。角色之间通过 Redis 锁互斥，确保同一角色不被并发推进。

### 3.2 五阶段闭环

```text
① 感知环境
   ├─ 读取角色状态（位置/精力/情绪/当前行为）
   ├─ 读取世界状态（时间/天气/场景）
   ├─ 读取周围角色（同位置的其他角色）
   └─ 记忆检索（从 pgvector 检索 Top-K 相关记忆）
        ↓
② 候选 Action 过滤
   └─ 遍历所有 Action，检查 precondition，生成候选列表
        ↓
③ LLM 决策
   ├─ 输入: 角色状态 + 世界状态 + 候选列表 + 检索到的记忆
   ├─ 模型: strong 类型（复杂决策）
   └─ 输出: 结构化决策 { action, reason, params, duration }
        ↓
④ Action 执行（单一 PG 事务）
   ├─ 更新 Redis 状态（位置/精力/行为）
   ├─ 写入 action_records
   └─ 生成 memory_episodes 存入 pgvector
        ↓
⑤ 记忆沉淀与反思触发
   ├─ 检查是否触发反思（如记忆数量达到阈值）
   └─ 检查是否需要调整计划
```

### 3.3 调度策略

| 策略 | 说明 |
|------|------|
| 角色级互斥锁 | Redis `SET char:{id}:lock NX EX 30`，确保单角色串行 |
| 并发上限 | 信号量限制同时进行的 Character Tick 数（默认 10） |
| 错过补偿 | 若某角色 Tick 耗时长，下一轮跳过该角色，避免雪崩 |
| 优先级 | 用户对话中的角色优先推进；空闲角色按轮询 |

### 3.4 伪代码

```python
async def character_tick_loop(engine: WorldEngine, character_id: UUID):
    while engine.running:
        # 1. 抢角色锁
        if not await engine.acquire_character_lock(character_id):
            await asyncio.sleep(engine.config.tick_seconds)
            continue

        try:
            async with start_span("character.tick", character_id=str(character_id)):
                await run_character_tick(engine, character_id)
        finally:
            await engine.release_character_lock(character_id)

        await asyncio.sleep(engine.config.tick_seconds)


async def run_character_tick(engine: WorldEngine, character_id: UUID):
    # ① 感知
    state = await engine.perceive(character_id)
    memories = await engine.retrieve_memories(character_id, state)

    # ② 候选 Action
    candidates = engine.filter_actions(state)

    # ③ LLM 决策
    decision = await engine.decide(state, candidates, memories)

    # ④ 执行（事务化）
    await engine.execute_action(character_id, decision)

    # ⑤ 反思/计划触发
    await engine.maybe_reflect(character_id)
    await engine.maybe_replan(character_id)
```

详细 Action 执行见 [Action系统设计](action-system.md)，记忆检索见 [记忆系统设计](memory-system.md)。

---

## 四、多智能体调度

### 4.1 角色间通信

角色不直接调用彼此，而是通过**事件广播**与**共享场景状态**间接交互：

| 机制 | 说明 | 示例 |
|------|------|------|
| 场景共享 | 同位置的角色互相可见，触发 `chat_with` 等 Action | 两人在咖啡店相遇 |
| 事件广播 | 重要事件发布到 Redis Stream，相关角色订阅 | 节日广播、突发事件 |
| 关系更新 | Action 执行后自动更新 `relations` 表 | `chat_with` 后 `strength +2` |

### 4.2 事件类型

```python
class EventType(Enum):
    WORLD_WEATHER_CHANGE = "world.weather_change"
    WORLD_EVENT_BROADCAST = "world.event_broadcast"
    CHARACTER_ARRIVE = "character.arrive"        # 进入某场景
    CHARACTER_LEAVE = "character.leave"
    CHARACTER_INTERACT = "character.interact"    # 与他人交互
    CHARACTER_SHARE = "character.share"          # 主动推送用户
```

### 4.3 事件总线（Redis Streams）

```text
Stream: events:world        # 世界事件
Stream: events:character    # 角色事件
消费组: character-tick-{character_id}  # 每角色独立消费组
```

角色 Tick 在感知阶段拉取自身消费组的事件，注入 LLM 上下文。

---

## 五、暂停 / 恢复 / 回放

### 5.1 暂停与恢复

```python
# 全局暂停
await engine.pause()        # running=False，所有循环退出
await engine.resume()       # running=True，重启循环

# 单角色暂停
await engine.pause_character(character_id)
```

### 5.2 状态回放

世界状态周期性快照到 PG `world_snapshots`。回放流程：

1. 选定快照 `tick_id`；
2. 从该快照恢复 Redis `world:state`；
3. 从 `action_records` 重放该 `tick_id` 之后的动作；
4. 可用于调试"角色为什么会做出某个决策"。

---

## 六、配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `world.tick_seconds` | 30 | World Tick 真实间隔 |
| `world.tick_minutes` | 10 | 每个 Tick 推进的虚拟分钟 |
| `world.weather_interval` | 60 | 每 60 Tick 更新一次天气 |
| `world.snapshot_interval` | 120 | 每 120 Tick 持久化一次快照 |
| `character.tick_seconds` | 30 | Character Tick 真实间隔 |
| `character.max_concurrent` | 10 | 并发角色 Tick 上限 |
| `character.lock_ttl_seconds` | 30 | 角色锁自动过期 |

配置文件格式见 [配置参考](config-reference.md)。

---

## 七、可观测埋点

| Span 名称 | 关键属性 |
|-----------|----------|
| `world.tick` | `tick_id`, `weather`, `time_advance` |
| `character.tick` | `character_id`, `tick_duration`, `decision_action` |
| `character.perceive` | `character_id`, `memories_retrieved` |
| `character.decide` | `character_id`, `candidates_count`, `model` |
| `character.execute` | `character_id`, `action_id`, `duration_minutes` |

详见 [可观测性设计](observability.md)。

---

## 八、相关文档

| 主题 | 文档 |
|------|------|
| Action 系统与执行闭环 | [action-system.md](action-system.md) |
| 记忆系统 | [memory-system.md](memory-system.md) |
| 数据模型 | [data-model.md](data-model.md) |
