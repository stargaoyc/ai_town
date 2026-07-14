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
    scene: str | None = None             # 所属场景（None 表示任意场景可执行）
    activity: str | None = None          # 对应场景声明的活动类型
    precondition: Callable[[State], bool] = None   # 前置条件判断（代码过滤）
    executor: Callable[[State, dict], State] = None # 执行逻辑（真实副作用）
    duration_minutes: int = 0            # 预计耗时（虚拟分钟）
    allow_dynamic_duration: bool = False # 是否允许 LLM 给出动态耗时
    energy_cost: int = 0                 # 精力消耗（-10 ~ +10）
    social_impact: int = 0               # 社交影响（-5 ~ +5）
    requires_llm: bool = False           # 是否需要 LLM 介入执行
    tags: list[str] = None               # 标签，用于候选过滤
```

> **关键约束**：Action 按**场景**组织（`scene` + `activity`），必须与 [小镇设计](town-design.md#三场景清单二次元小镇风) 中场景声明的 `activities` 对应。`precondition` 由代码过滤候选，**LLM 只能在候选中选择，不能绕过 precondition 声明角色已做某事**。

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

| 分类 | 示例 Action | Scene | Precondition | 副作用 |
|------|-------------|-------|--------------|--------|
| 移动 | `move_home_to_school` | — | 在家 && 工作日 && 8:00-9:00 | location→学校 |
| 生活 | `eat_meal` | home/cafe | 饥饿度>70 && 有食物 | satiety+30, stamina+5 |
| 生活 | `sleep` | home | 精力<30 && 在家 && 作息睡眠窗口 | stamina+50 |
| 生活 | `use_phone` | 任意 | phone_battery>10 && 非睡眠 | phone_battery-15, social_energy+5 |
| 生活 | `charge_phone` | home | phone_battery<20 && 在家 | phone_battery+60 |
| 工作 | `study` | school/library | 在校/馆 && 9:00-17:00 | knowledge+5 |
| 工作 | `work_parttime` | cafe/bookstore | 在店 && 有排班 | money+10 |
| 社交 | `chat_with` | 任意 | social_energy≥10 | social_energy-10；双向关系更新（陌生人破冰+2 / 其他+5）；双方各写一条 `source_type=conversation` 记忆 |
| 工具 | `search_info` | 任意 | 无限制 | 调用 MCP 搜索 |
| 节日 | `watch_fireworks` | shrine/coast | 樱花祭/夏日祭期间 | mood+10 |

---

## 三、执行闭环

### 3.1 闭环阶段

```text
① LLM 决策
   ├─ 输入: 角色状态 + 世界状态 + 候选 Action 列表 + 检索到的记忆 + 计划
   ├─ 模型: strong 类型
   └─ 输出: 结构化决策（见 §3.2）
        ↓
② Action 执行（单一 PG 事务）
   ├─ 调用 action.executor(state, params) 计算新状态
   ├─ 更新 Redis 实时状态
   ├─ 写入 action_records（行为记录）
   ├─ 生成 memory_episodes（记忆向量，存入 pgvector）
   ├─ 更新 relations（若涉及社交）
   └─ 应用 planChanges（若决策建议计划变更）
        ↓
③ 后置触发
   ├─ 检查是否触发反思（memory_episodes 累计阈值）
   ├─ 检查是否需要调整计划
   ├─ 触发 Action 完成事件（completion event）
   └─ 检查 proactiveShareIntent，决定是否主动分享给用户
```

### 3.2 结构化决策结果

LLM 决策返回的是一份**结构化决策结果**，只说明"本轮选择哪个 Action，以及为什么"，本身**不直接改变角色状态**。状态变化由后续 Action executor 完成。

```jsonc
{
  // 本轮选择的 Action，只能来自当前可执行候选列表。
  "action": "study_at_library",
  // 选择理由，会进入行为记录，也会影响后续上下文。
  "reason": "下午没课，去图书馆复习数学，准备期末考试。",
  // 少数 Action（allow_dynamic_duration=true）可由 Agent 给出动态耗时；其余可省略。
  "durationMinute": 60,
  // 当本次行动影响长期或短期计划时返回；没有计划变化时省略。
  "planChanges": [
    { "type": "progress_short_term_plan", "title": "今天完成数学作业", "delta": 30 }
  ],
  // 当本次行动值得主动分享时返回；后续链路会再走一次 LLM 判断是否适合分享并生成文案。
  "proactiveShareIntent": {
    "shouldShare": true,
    "reason": "今天在图书馆遇到一道很有意思的题，可以和用户分享一下。"
  }
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `action` | 是 | 只能来自候选列表，否则视为非法决策 |
| `reason` | 是 | 进入行为记录与后续记忆上下文 |
| `durationMinute` | 否 | 仅 `allow_dynamic_duration=true` 的 Action 生效；受 [动态耗时](world-engine.md#动态耗时系统) 调整 |
| `planChanges` | 否 | 计划变更建议，由业务流程写入 `plans` 表 |
| `proactiveShareIntent` | 否 | 主动分享意图，触发分享链路 |

> **决策边界**：LLM 不应判断未进入候选列表的 Action，也不应绕过 `precondition` 直接声明角色已做某事。详见 §七 LLM 决策边界。

#### planChanges 类型

| type | 说明 |
|------|------|
| `create_long_term_plan` | 创建长期计划（建议，由业务流程写入） |
| `create_short_term_plan` | 创建短期计划 |
| `progress_short_term_plan` | 推进短期计划进度（`delta` 字段） |
| `complete_short_term_plan` | 完成短期计划 |
| `abandon_plan` | 放弃计划 |

> LLM 只能**建议**计划变更，真正的 `plans` 表写入由业务流程执行，避免 LLM 绕过事实记录。详见 [角色设计 - 计划系统](character-design.md#五计划系统plan)。

#### 主动分享链路

`proactiveShareIntent.shouldShare=true` 时，触发分享链路：

```text
Action 执行完成
   ↓
proactiveShareIntent.shouldShare?
   ├─ false → 结束
   └─ true  → 再走一次 LLM（flash 模型）
              ├─ 输入: Action 结果 + 角色状态 + 世界状态 + 群聊/私聊上下文
              ├─ 判断: 是否真的适合分享（避免刷屏）
              └─ 输出: 自然语言分享文案
                        ↓
              由消息 handler 决定发送到哪个平台（不暴露工程概念）
```

### 3.3 事务化保证

**事务化保证**：以下操作在**同一个 PG 事务**中完成，任一失败则整体回滚：

| 操作 | 目标 |
|------|------|
| 写 `action_records` | 行为记录持久化 |
| 写 `memory_episodes`（含 embedding） | 记忆向量持久化 |
| 更新 `relations`（社交场景） | 关系图谱更新 |
| 更新 `character_states`（PG 镜像） | 持久状态对齐 |

Redis 实时状态在事务提交后再写入（最终一致），失败时由 PG 镜像回灌。

### 3.4 执行器伪代码

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

### 3.5 多智能体交互：chat_with

`chat_with` 是参数化 SOCIAL Action，`params.target_character_id` 指定对话对象。执行层（`CharacterTickEngine._handle_character_chat`）在状态变更前完成多智能体交互闭环：

1. **同场景校验**：目标必须在 `nearby_characters`（同 `location`）中，否则降级为 `wait`，不阻塞 Tick；
2. **LLM 对话生成**：基于双方性格、关系亲密程度、当前情绪与场景，生成一段简短对话（双方各一两句，60–200 字），不暴露 Action/system 等工程概念；
3. **双向关系更新**：通过 `RelationGraph.update_on_interaction` 同步更新双方关系，陌生人破冰 `+2`，其他关系 `+5`；
4. **双记忆持久化**：为发起方与对方各写入一条 `MemoryEpisode`（`source_type=conversation`，第一人称视角），让两人都"记得"这次对话；
5. **对话回放**：生成的对话文本写入 `ActionRecord.result`，`related_characters` 记录对方 ID，供前端展示与关系溯源。

> 对话生成失败时降级为 `wait`，关系/记忆写入失败不中断 Tick 主流程。同场景角色列表由 `GET /api/v1/characters/{id}/nearby` 提供（详见 [API 设计文档](api-spec.md)）。

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
当前状态: 位置={location}, 精力={stamina}, 饱腹={satiety}, 情绪={mood}, 手机电量={phone_battery}

[世界状态]
时间: {world_time}
天气: {weather}
场景: {scenes}（含开放状态与拥挤度）
节日: {active_events}

[相关记忆]
{memories_top_k}

[当前计划]
长期: {long_term_plans}
短期: {short_term_plans}

[候选 Action]
1. {action_id}: {action_name} — {description} [体力{energy_cost}][耗时{duration}分钟]
2. ...

[输出格式]
请输出 JSON，action 只能来自候选列表：
{
  "action": "<action_id>",
  "reason": "<理由>",
  "durationMinute": <分钟，仅部分 Action 可填>,
  "planChanges": [...],          // 可选
  "proactiveShareIntent": {...}  // 可选
}
```

输出强制结构化，便于解析与审计。面向用户的最终消息**不能**暴露 Action、schema、字段名等工程概念。

---

## 七、参数化 Action

部分 Action 需要 LLM 提供参数（如 `chat_with` 的对话对象、`buy_item` 的商品名）。参数化 Action 让**参数来源、校验和执行副作用保持可见**。

### 7.1 参数声明

```python
@dataclass
class ActionParam:
    name: str                       # 参数名
    type: str                       # string | int | enum
    required: bool = True
    enum_values: list[str] = None   # type=enum 时的可选值
    description: str = ""           # 给 LLM 的参数说明
```

### 7.2 参数校验

参数在进入 executor 前由代码校验，**不信任 LLM 输出**：

```python
def validate_params(action: Action, params: dict) -> dict:
    validated = {}
    for p in action.params:
        if p.required and p.name not in params:
            raise IllegalDecision(f"缺少参数 {p.name}")
        if p.type == "enum" and params[p.name] not in p.enum_values:
            raise IllegalDecision(f"非法参数值 {p.name}={params[p.name]}")
        validated[p.name] = params[p.name]
    return validated
```

> 非法参数会被拒绝，本轮决策回退为"等待"或重新决策。详见 §九 LLM 决策边界。

---

## 八、Action 完成事件

Action 执行完成后发布**完成事件**，供多智能体调度与消息系统消费：

```python
class ActionCompleted(Event):
    character_id: UUID
    action_id: str
    result: dict                    # 执行结果摘要
    location: str
    related_characters: list[UUID]
    timestamp: datetime
```

| 消费方 | 用途 |
|--------|------|
| 多智能体调度 | 同场景角色感知到"某人到达/离开" |
| 消息系统 | 触发主动分享评估 |
| 记忆系统 | 沉淀为 MemoryEpisode |
| 反思触发器 | 累计未反思记忆数 |

事件通过 Redis Streams 广播，详见 [世界引擎 - 事件总线](world-engine.md#43-事件总线redis-streams)。

---

## 九、LLM 决策边界

> 核心原则：LLM 是决策和生成能力，**不是状态真相源**。

| 能做 | 不能做 |
|------|--------|
| 在候选 Action 中选择 | 判断未进入候选列表的 Action |
| 给出选择理由 | 绕过 `precondition` 声明角色已做某事 |
| 提供参数（参数化 Action） | 直接修改 Redis / PG / 文件状态 |
| 建议计划变更（`planChanges`） | 直接写入 `plans` 表 |
| 表达分享意图（`proactiveShareIntent`） | 决定发送到哪个平台 |
| 给出动态耗时（`allow_dynamic_duration`） | 绕过天气/拥挤度的耗时调整规则 |

### 9.1 非法决策处理

```python
async def decide(state, candidates, memories) -> Decision:
    raw = await llm.decide(prompt(state, candidates, memories))
    action_id = raw["action"]
    if action_id not in [c.id for c in candidates]:
        raise IllegalDecision(f"非候选 Action: {action_id}")
    if "durationMinute" in raw:
        action = registry.get(action_id)
        if not action.allow_dynamic_duration:
            raise IllegalDecision(f"Action 不允许动态耗时: {action_id}")
    return Decision(**raw)
```

非法决策会记录到 Trace（`decision.illegal`），本轮回退为"等待"或重试（最多 2 次）。

### 9.2 真相源约定

| 数据 | 真相源 | 写入方 |
|------|--------|--------|
| 角色实时状态 | Redis | Action executor |
| 行为历史 | PG `action_records` | Action executor（事务） |
| 记忆事实 | PG `memory_episodes` | Action executor / 对话沉淀 |
| 计划 | PG `plans` | 业务流程（应用 `planChanges`） |
| 关系 | PG `relations` | Action executor（社交类） |

> LLM 输出的任何内容都不会直接成为状态，必须经过业务流程显式写入。

---

## 十、可观测埋点

| Span | 关键属性 |
|------|----------|
| `action.decision` | `character_id`, `action_name`, `reason`, `model`, `has_plan_changes`, `should_share` |
| `action.execute` | `action_id`, `duration_minutes`, `success`, `tx_id` |
| `action.memory.write` | `character_id`, `importance`, `embedding_dim` |
| `action.share` | `character_id`, `platform`, `accepted` |
| `decision.illegal` | `character_id`, `reason`, `retry_count` |

---

## 十一、相关文档

| 主题 | 文档 |
|------|------|
| 角色设计 | [character-design.md](character-design.md) |
| 小镇与场景 | [town-design.md](town-design.md) |
| 世界引擎与角色 Tick | [world-engine.md](world-engine.md) |
| 记忆系统 | [memory-system.md](memory-system.md) |
| 模块与 MCP | [module-system.md](module-system.md) |
| 数据模型 | [data-model.md](data-model.md) |
