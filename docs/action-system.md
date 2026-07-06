# Action 系统设计

> Action 是角色的原子行为单元。本文档定义 Action 的数据结构、分类、执行闭环、注册机制与事务化保证。

---

## 一、Action 定义

### 1.1 数据结构

```python
from dataclasses import dataclass
from typing import Callable, Any

@dataclass
class Action:
    id: str                              # 唯一标识
    name: str                            # 显示名称
    category: ActionCategory             # MOVE / LIFE / WORK / SOCIAL / TOOL
    precondition: Callable[[State], bool]      # 前置条件判断
    executor: Callable[[State, dict], State]   # 执行逻辑
    duration_minutes: int                # 预计耗时
    energy_cost: int                     # 精力消耗（-10 ~ +10）
    social_impact: int                   # 社交影响（-5 ~ +5）
    requires_llm: bool = False           # 是否需要 LLM 介入执行
    tags: list[str] = None               # 标签，用于候选过滤
```

### 1.2 Action 分类

| 分类 | 标识 | 说明 |
|------|------|------|
| 移动 | `MOVE` | 改变角色位置 |
| 生活 | `LIFE` | 进食、睡眠、休息等生理行为 |
| 工作 | `WORK` | 学习、工作、生产 |
| 社交 | `SOCIAL` | 与其他角色交互 |
| 工具 | `TOOL` | 调用 MCP 工具（搜索、代码执行等） |

---

## 二、Action 分类与示例

| 分类 | 示例 Action | Precondition | 副作用 |
|------|-------------|--------------|--------|
| 移动 | `move_home_to_school` | 在家 && 工作日 && 8:00-9:00 | location→学校 |
| 移动 | `move_to_cafe` | 任意 && cafe 开放 | location→咖啡店 |
| 生活 | `eat_meal` | 饥饿度>70 && 有食物 | hunger-30, energy+10 |
| 生活 | `sleep` | 精力<30 && 在家 | energy+50 |
| 工作 | `study` | 在学校 && 9:00-17:00 | knowledge+5 |
| 工作 | `work_parttime` | 在咖啡店 && 有排班 | money+10 |
| 社交 | `chat_with` | 同位置有其他角色 | relationship+2 |
| 工具 | `search_info` | 无限制 | 调用 MCP 搜索 |

---

## 三、执行闭环

### 3.1 闭环阶段

```text
① LLM 决策
   ├─ 输入: 角色状态 + 世界状态 + 候选 Action 列表 + 检索到的记忆
   ├─ 模型: strong 类型
   └─ 输出: 结构化决策
        {
          "action": "move_to_cafe",
          "reason": "感到疲惫，想去咖啡店放松",
          "params": { "target": "cafe" },
          "duration": 15
        }
        ↓
② Action 执行（单一 PG 事务）
   ├─ 调用 action.executor(state, params) 计算新状态
   ├─ 更新 Redis 实时状态
   ├─ 写入 action_records（行为记录）
   ├─ 生成 memory_episodes（记忆向量，存入 pgvector）
   └─ 更新 relations（若涉及社交）
        ↓
③ 后置触发
   ├─ 检查是否触发反思（memory_episodes 累计阈值）
   ├─ 检查是否需要调整计划
   └─ 检查 proactiveShareIntent，决定是否主动推送用户
```

### 3.2 事务化保证

**核心改进（去 MongoDB 后）**：以下操作在**同一个 PG 事务**中完成，任一失败则整体回滚，杜绝跨库半写：

| 操作 | 目标 |
|------|------|
| 写 `action_records` | 行为记录持久化 |
| 写 `memory_episodes`（含 embedding） | 记忆向量持久化 |
| 更新 `relations`（社交场景） | 关系图谱更新 |
| 更新 `character_states`（PG 镜像） | 持久状态对齐 |

Redis 实时状态在事务提交后再写入（最终一致），失败时由 PG 镜像回灌。

### 3.3 执行器伪代码

```python
async def execute_action(db: DB, redis: Redis, character_id: UUID, decision: Decision):
    """Action 执行的原子化闭环"""
    embedding_vec = await embed(episode_text(character_id, decision))

    async with db.session() as session:
        action_repo = ActionRepository(session)
        memory_repo = MemoryRepository(session)
        relation_repo = RelationRepository(session)
        state_repo = CharacterStateRepository(session)

        # 1. 读取当前状态
        state = await state_repo.get(character_id)

        # 2. 调用 Action executor 计算新状态
        action = action_registry.get(decision.action)
        new_state = action.executor(state, decision.params)

        # 3. 写行为记录
        record = ActionRecord(
            character_id=character_id,
            action_id=action.id,
            action_name=action.name,
            params=decision.params,
            reason=decision.reason,
            result=describe_result(new_state),
            duration_minutes=decision.duration,
            location=new_state.location,
            related_characters=new_state.related_characters or [],
        )
        await action_repo.add(record)

        # 4. 写记忆向量（同一事务）
        episode = MemoryEpisode(
            character_id=character_id,
            content=f"{character.name} 在 {new_state.location} 执行了 {action.name}。理由: {decision.reason}",
            embedding=embedding_vec,
            importance=estimate_importance(decision, action),
            timestamp=now(),
            action_id=action.id,
            location=new_state.location,
            related_characters=new_state.related_characters or [],
        )
        await memory_repo.add(episode)

        # 5. 更新关系（社交场景）
        if action.category == ActionCategory.SOCIAL:
            for other_id in new_state.related_characters:
                await relation_repo.adjust(character_id, other_id, action.social_impact)

        # 6. 更新 PG 状态镜像
        await state_repo.update(character_id, new_state)

        # 事务在 contextmanager 退出时自动 commit

    # 7. 事务提交后再写 Redis 实时状态
    await redis.hset(f"char:{character_id}:state", mapping=new_state.to_dict())

    # 8. 后置触发（异步）
    await schedule_post_action_hooks(character_id, episode)
```

---

## 四、Action 注册机制

### 4.1 注册接口

```python
class ActionRegistry:
    def register(self, action: Action) -> None: ...
    def unregister(self, action_id: str) -> None: ...
    def get(self, action_id: str) -> Action: ...
    def list_by_category(self, category: ActionCategory) -> list[Action]: ...
    def filter_candidates(self, state: State) -> list[Action]:
        """遍历所有 Action，检查 precondition，返回候选列表"""
        return [a for a in self._actions.values() if a.precondition(state)]
```

### 4.2 注册方式

| 方式 | 说明 | 适用 |
|------|------|------|
| 内置注册 | 启动时在 `core/actions/` 注册核心 Action | 移动/生活/工作/社交 |
| 模块贡献 | 模块启用时贡献 Action（如 MCP 工具对应的 Action） | 工具类 Action |
| 配置加载 | 从 `config.yaml` 加载自定义 Action | 业务可配置 |

### 4.3 自定义 Action 示例

```python
# core/actions/move.py
from core.action_system import Action, ActionCategory

def register_move_actions(registry: ActionRegistry):
    registry.register(Action(
        id="move_home_to_school",
        name="去学校",
        category=ActionCategory.MOVE,
        precondition=lambda s: (
            s.location == "home"
            and is_workday(s.world_time)
            and 8 * 60 <= minute_of_day(s.world_time) <= 9 * 60
        ),
        executor=lambda s, p: s.replace(location="school"),
        duration_minutes=15,
        energy_cost=-5,
        social_impact=0,
    ))
```

---

## 五、Action 与模块的关系

部分 Action（`TOOL` 类）依赖模块管理器提供的 MCP 工具：

```text
模块启用 → 模块管理器注册工具 → ActionRegistry 注册对应 TOOL Action
        ↓                          ↓
    模块禁用 → 模块管理器注销工具 → ActionRegistry 注销对应 Action
```

详见 [模块与MCP系统设计](module-system.md)。

---

## 六、决策 Prompt 结构

LLM 决策的输入 Prompt 包含：

```text
[角色档案]
姓名: {name}
性格: {personality}
背景: {backstory}
当前状态: 位置={location}, 精力={energy}, 饥饿={hunger}, 情绪={mood}

[世界状态]
时间: {world_time}
天气: {weather}
场景: {scenes}

[相关记忆]
{memories_top_k}

[候选 Action]
1. {action_id}: {action_name} — {description}
2. ...

[输出格式]
请输出 JSON:
{ "action": "<action_id>", "reason": "<理由>", "params": {...}, "duration": <分钟> }
```

输出强制结构化，便于解析与审计。

---

## 七、可观测埋点

| Span | 关键属性 |
|------|----------|
| `action.decision` | `character_id`, `action_name`, `reason`, `model` |
| `action.execute` | `action_id`, `duration_minutes`, `success`, `tx_id` |
| `action.memory.write` | `character_id`, `importance`, `embedding_dim` |

---

## 八、相关文档

| 主题 | 文档 |
|------|------|
| 世界引擎与角色 Tick | [world-engine.md](world-engine.md) |
| 记忆系统 | [memory-system.md](memory-system.md) |
| 模块与 MCP | [module-system.md](module-system.md) |
| 数据模型 | [data-model.md](data-model.md) |
