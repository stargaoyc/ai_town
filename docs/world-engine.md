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

World Tick 通过**可配置的演化列表（Evolution List）**驱动：每一轮读取当前世界状态，依次交给各 Evolution 项计算下一份世界状态，再写回存储。新增世界规则时只需补一个 Evolution 项，无需改动主循环。

| Evolution 项 | 说明 | 更新频率 |
|--------------|------|----------|
| `TimeEvolution` | 虚拟时钟按 Tick 步进（如每 30 秒推进 10 分钟） | 每 Tick |
| `WeatherEvolution` | 晴天/多云/阴雨/雪/大风，影响角色行为选择 | 每 60 Tick |
| `SceneEvolution` | 各地点（咖啡店、学校、公园）开放/关闭/拥挤度 | 每 Tick |
| `ResourceEvolution` | 城镇资源（食物、能源）动态增减 | 每 Tick |
| `EventEvolution` | 节日/突发事件触发与广播 | 按日期 |

### 2.3 设计约束

- World Tick **不依赖用户消息**——世界在用户不在时依然运转。
- World Tick **不直接修改角色状态**，只修改世界状态（天气、场景、资源、事件）。
- 世界实时态存 Redis，周期性差分事件存 PG `world_events`（用于回放/调试）。

### 2.4 伪代码

```python
async def world_tick_loop(engine: WorldEngine):
    while engine.running:
        async with engine.tick_lock():
            tick_id = engine.next_tick_id()
            span = start_span("world.tick", tick_id=tick_id)

            # 1. 读取当前世界状态
            state = await engine.load_world_state()

            # 2. 依次执行 Evolution 列表，计算下一份世界状态
            for evolution in engine.evolutions:        # 可配置的演化项
                if evolution.should_run(tick_id, state):
                    state = await evolution.apply(state, tick_id)

            # 3. 写回 Redis 实时态
            await engine.save_world_state(state)

            # 4. 持久化快照（每 N Tick 一次）
            if tick_id % engine.config.snapshot_interval == 0:
                await engine.persist_snapshot(state)

            # 5. 广播给订阅的 WebSocket 客户端
            await engine.broadcast_state(state)

            span.end()

        await asyncio.sleep(engine.config.tick_seconds)
```

### 2.5 Evolution 接口

```python
class WorldEvolution(Protocol):
    """世界演化项：读取当前状态，返回更新后的状态。"""
    def should_run(self, tick_id: int, state: WorldState) -> bool: ...
    async def apply(self, state: WorldState, tick_id: int) -> WorldState: ...
```

新增世界规则（如季节系统、物价波动）只需实现 `WorldEvolution` 并注册到 `engine.evolutions`，主循环不变。

### 2.6 世界状态结构（Redis Hash）

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

详细字段定义见 [数据模型设计](data-model.md#310-world_events世界变更事件差分记录)。

### 2.7 作息系统

角色卡 `traits.schedule` 声明作息类型，World Tick 不直接修改角色状态，但 `TimeEvolution` 推进时钟后，Character Tick 会结合作息窗口判断可执行行为。

| 作息类型 | 起床窗口 | 睡眠窗口 | 影响 |
|----------|----------|----------|------|
| `early_bird` | 05:30–07:00 | 21:00–23:00 | 早起 Action 候选；晚于 23:00 强烈倾向睡眠 |
| `normal` | 06:30–08:00 | 22:30–00:00 | 标准作息 |
| `night_owl` | 09:00–11:00 | 01:00–03:00 | 夜间 Action 候选；早晨精力恢复慢 |

> 作息只影响 Action 候选过滤与决策 Prompt，不强制改状态。角色仍可能因剧情熬夜，但代价是次日 `stamina` 恢复减少。

### 2.8 动态耗时系统

Action 的 `duration_minutes` 默认是静态值，但部分 Action 允许 LLM 在决策时给出**动态耗时**，或由世界状态动态调整。

| 场景 | 调整规则 |
|------|----------|
| 室外移动 + 恶劣天气 | 静态耗时 ×1.5（rainy/snowy/windy） |
| 拥挤场景购物 | 耗时 ×1.2（`crowdedness > 80`） |
| LLM 动态耗时 | 决策结果 `durationMinute` 字段覆盖默认值（少数 Action 允许） |

```python
def resolve_duration(action: Action, decision: Decision, state: WorldState) -> int:
    base = decision.duration_minute or action.duration_minutes
    if action.category == ActionCategory.MOVE and not action.indoor:
        if state.weather in ("rainy", "snowy", "windy"):
            base = int(base * 1.5)
    if state.crowdedness(action.scene) > 80:
        base = int(base * 1.2)
    return base
```

### 2.9 天气影响矩阵

天气不仅影响场景拥挤度（详见 [小镇设计 - 天气与场景联动](town-design.md#八天气与场景联动)），也进入角色决策 Prompt，影响行为偏好：

| 天气 | Prompt 描述影响 | 行为偏好 |
|------|------------------|----------|
| sunny | "阳光明媚，适合外出" | 户外放松/运动 Action 加分 |
| rainy | "下雨了，室外不便" | 室内 Action 优先；移动耗时 ×1.5 |
| snowy | "下雪了，有些冷" | 室内优先；触发打雪仗等季节 Action |
| windy | "风很大" | 海岸/森林 Action 减分 |

天气描述由 `WeatherEvolution` 写入 `world:state.weather`，Character Tick 感知阶段注入决策上下文。

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

世界状态周期性差分事件存 PG `world_events`。回放流程：

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
| 角色设计 | [character-design.md](character-design.md) |
| 小镇与场景 | [town-design.md](town-design.md) |
| Action 系统与执行闭环 | [action-system.md](action-system.md) |
| 记忆系统 | [memory-system.md](memory-system.md) |
| 数据模型 | [data-model.md](data-model.md) |

---

## 附：2026-08-24 Tick 流程增补

Character Tick 在「记忆沉淀」后新增两步（详见 [认知深化与群体动力学](cognition-and-group-dynamics.md)）：

- **5.5 传闻传播**：好友高重要性经历 -> 听者第二手记忆（GossipService，失败不阻断）；
- **群活动分支**：Action `group_activity` 命中且同场景 >=3 人时生成集体叙事，
  为全体参与者写共同经历记忆并两两关系 +2；人数门槛在候选过滤阶段执行。
