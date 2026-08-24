# 角色设计

> 本文档定义 AI Town 中"角色"的完整设计：核心理念、角色档案、实时状态、记忆模型、计划系统、关系图谱与角色卡配置。
>
> 设计哲学（参考 yuiju）：**不做 AI 智能助手，做有自己生活的"人"**。角色不是随叫随到的工具，而是在自己的世界里持续生活，并偶尔与用户相遇。

---

## 一、核心理念

| 原则 | 说明 |
|------|------|
| 状态驱动 | LLM 是决策和生成能力，不是状态真相源；所有状态变更由 Action executor 显式执行 |
| 事实优先 | 所有可追溯事实必须落到 `memory_episodes` 或明确的状态字段中，不能只活在 Prompt 里 |
| 闭环演化 | 行为沉淀为记忆 → 记忆影响未来决策 → 形成可追溯的生活轨迹 |
| 生活感 | 角色回复与近况来自她真实发生过的经历，而非临时现编的人设文本 |
| 独立性 | 角色在世界持续运行，用户不在时依然生活；聊天只是观察和介入角色生活的窗口 |

> 关键边界：LLM 可以选择 Action、生成消息、整理日记、总结记忆；但 LLM **不直接修改** Redis / PG / 文件状态。所有状态变化必须由业务流程或 Action executor 显式执行。

---

## 二、角色档案（Profile）

角色档案是角色的**静态身份信息**，存于 PG `characters` 表，生命周期内基本不变。

### 2.1 字段定义

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID v7 | 主键 |
| `name` | str | 姓名 |
| `age` | int | 年龄 |
| `occupation` | str | 职业/身份（高中生/咖啡师/书店店员/巫女…） |
| `personality` | list[str] | 性格标签（开朗/细心/社恐/元气…） |
| `traits` | jsonb | 特质字典（hobby / favorite_color / schedule / favorite_food…） |
| `backstory` | text | 背景故事 |
| `avatar_url` | str | 头像 |
| `voice_preset` | str | 语音预设（可选，TTS 用） |
| `is_active` | bool | 是否参与世界推进 |

### 2.2 性格标签体系

性格标签不只是展示文本，会**进入决策 Prompt** 影响行为选择。建议从以下维度组合：

| 维度 | 示例标签 |
|------|----------|
| 外向性 | 开朗 / 元气 / 社恐 / 内向 / 温柔 |
| 责任感 | 细心 / 懒散 / 守时 / 拖延 |
| 情绪基调 | 乐观 / 多愁善感 / 平和 / 暴躁 |
| 兴趣倾向 | 喜欢咖啡 / 热爱阅读 / 痴迷画画 / 宅 |

### 2.3 特质字典（traits）

`traits` 是 JSONB，承载结构化的偏好与作息：

```json
{
  "hobby": ["咖啡拉花", "画画", "看轻小说"],
  "favorite_color": "blue",
  "schedule": "early_bird",           // early_bird | night_owl | normal
  "favorite_food": ["蛋包饭", "草莓大福"],
  "dislikes": ["香菜", "吵闹"],
  "mbti": "INFP"
}
```

`schedule` 字段会接入世界引擎的**作息系统**，影响睡眠/起床时间窗口（详见 [世界引擎 - 作息系统](world-engine.md#作息系统)）。

---

## 三、角色实时状态（Real-time State）

实时状态是角色的**动态事实**，存于 Redis Hash `char:{id}:state`，是决策与展示的真相源。

### 3.1 状态字段

| 字段 | 范围 | 说明 |
|------|------|------|
| `location` | scene_id | 当前所在场景 |
| `stamina` | 0–100 | 体力（< 30 倾向休息） |
| `satiety` | 0–100 | 饱腹度（> 70 触发进食需求） |
| `mood` | enum | happy / calm / sad / anxious / excited / tired / angry |
| `money` | int | 金币 |
| `inventory` | jsonb | 背包（物品 → 数量） |
| `current_action` | jsonb | { action_id, started_at, ends_at } |
| `phone_battery` | 0–100 | 手机电量（现代元素，< 20 触发充电） |
| `social_energy` | 0–100 | 社交能量（社恐角色消耗更快） |

### 3.2 状态真相源约定

| 数据 | 真相源 | 说明 |
|------|--------|------|
| 实时状态 | **Redis** | 角色实时状态唯一真相源 |
| 状态镜像 | PG `character_states` | 周期性对齐，用于回放/审计 |
| 行为历史 | PG `action_records` | 不可变事实记录 |
| 记忆事实 | PG `memory_episodes` + pgvector | 可追溯经历事件 |

> Redis 是实时状态真相源，PG 是历史与可追溯记录真相源。Action 执行时**先写 PG 事务，再写 Redis**（事务提交后），失败时由 PG 镜像回灌。

### 3.3 状态变化规则

状态只能通过以下途径变更，**禁止 LLM 直接声明状态**：

1. **Action executor**：行为执行的副作用（如 `eat_meal` → `satiety +30`）
2. **World Tick 演化**：全局影响（如天气变冷 → 全员 `stamina -2`）
3. **管理 API**：人工干预（调试/恢复）

---

## 四、角色记忆模型

> 记忆是角色的灵魂所在。参考 yuiju：记忆分为**可追溯事实**和**派生记忆**两类。

```text
┌─────────────────────────────────────────────────────────┐
│                MemoryEpisode (经历事实)                  │
│   不可变 · 可追溯 · 含向量 · 行为/对话/事件沉淀           │
└──────────────┬───────────────────────┬──────────────────┘
               │                       │
      ┌────────▼────────┐    ┌─────────▼─────────┐
      │   Diary (日记)   │    │  派生记忆检索      │
      │ 叙事归档 · 分层  │    │ Person / Plan     │
      └─────────────────┘    └───────────────────┘
```

### 4.1 MemoryEpisode（经历事实）

- **定义**：已经发生的、可追溯的经历事件，是不可变事实记录。
- **来源**：Action 执行沉淀、对话窗口沉淀、世界事件触发。
- **存储**：PG `memory_episodes` + pgvector 向量索引。
- **检索**：Character Tick 感知阶段，按角色 + 语义相似度 + 重要性 + 时间衰减混合排序取 Top-K。
- **禁止**：不要把临时日志、调试信息或 UI 展示状态写成 Episode。

### 4.2 派生记忆

派生记忆**不替代事实记录**，是基于 Episode 或对话生成的二次结构化记忆：

| 类型 | 说明 | 触发时机 |
|------|------|----------|
| **人物记忆（Person Memory）** | 对特定用户/角色的印象与关键事件 | 对话窗口沉淀、社交 Action 后 |
| **计划记忆（Plan Memory）** | 与长期/短期计划相关的记忆 | 计划创建/推进/完成时 |
| **反思（Reflection）** | 从多条 Episode 归纳出的高层认知 | 累计 N 条未反思记忆触发 |

### 4.3 日记（Diary）—— 分层归档

参考 yuiju 的分层日记设计，避免日记变成流水账或重复 Episode：

| 层级 | 生成时机 | 内容 |
|------|----------|------|
| 当日详情 | 每虚拟日结束时 | 基于当日 Episode 生成叙事化日记 |
| 周期摘要 | 每虚拟周 | 归纳本周值得记住的事 |
| 长期归档 | 每虚拟月 | 提炼阶段性成长与变化 |

> Diary 是叙事文本，**不替代 Episode 真相源**。检索回忆时优先用 Episode 向量，Diary 作为补充上下文。

详见 [记忆系统设计](memory-system.md)。

---

## 五、计划系统（Plan）

角色拥有**长期计划**与**短期计划**，让行为不只是被动回应，而是朝目标推进。

### 5.1 计划结构

```python
@dataclass
class Plan:
    id: UUID
    character_id: UUID
    type: Literal["long_term", "short_term"]
    title: str                       # "期末考试进前10" / "今天完成数学作业"
    description: str
    status: Literal["active", "completed", "abandoned"]
    priority: int                    # 1-5
    deadline: datetime | None        # 截止时间（虚拟时间）
    progress: int                    # 0-100
    created_at: datetime
    related_memory_ids: list[UUID]   # 关联的 Episode
```

### 5.2 计划生命周期

```text
创建(active) ──推进──► 推进(active) ──完成──► completed
                          │
                          └──放弃──► abandoned
```

| 事件 | 触发方式 |
|------|----------|
| 创建 | LLM 在决策时建议新计划，由业务流程写入 `plans` 表 |
| 推进 | Action 执行返回 `planChanges`，更新 `progress` |
| 完成 | `planChanges.type = complete_short_term_plan` |
| 调整 | 反思触发重新规划（`maybe_replan`） |

### 5.3 计划与 Action 的联动

Action 决策结果可携带**计划变更建议**（详见 [Action 系统 - 结构化决策](action-system.md#结构化决策结果)）：

```json
{
  "action": "study_at_library",
  "reason": "下午没课，去图书馆复习数学，准备期末考试。",
  "planChanges": [
    { "type": "progress_short_term_plan", "title": "今天完成数学作业", "delta": 30 }
  ]
}
```

> LLM 只能**建议**计划变更，真正的 `plans` 表写入由业务流程执行，避免 LLM 绕过事实记录。

---

## 六、关系图谱

角色与角色、角色与用户之间的关系存于 PG `relations` 表。

### 6.1 关系字段

| 字段 | 说明 |
|------|------|
| `character_id` / `target_id` | 双向关系 |
| `strength` | 亲密度 0–100 |
| `relationship_type` | friend / classmate / colleague / acquaintance / rival |
| `last_interaction_at` | 最近交互时间 |
| `notes` | 关系备注（如"在咖啡店认识的"） |

### 6.2 关系更新规则

- 社交类 Action（`chat_with` / `share_gift`）执行后自动更新 `strength`。
- `strength` 增量由 Action 的 `social_impact` 决定，受双方性格调节（社恐角色增长更慢）。
- 长时间无交互自然衰减（每周 `-1`，下限 0）。

---

## 七、角色卡配置（YAML）

角色卡支持从 YAML 文件批量导入，便于二次元风格角色的快速创建。

### 7.1 配置示例

```yaml
# configs/characters/yuina.yaml
name: 结衣奈
age: 17
occupation: 高中生
personality:
  - 温柔
  - 细心
  - 有点社恐
  - 喜欢画画
traits:
  hobby: [咖啡拉花, 水彩画, 看轻小说]
  favorite_color: sakura_pink
  schedule: early_bird
  favorite_food: [草莓大福, 蛋包饭]
  dislikes: [香菜, 吵闹]
  mbti: INFP
backstory: |
  从小在海边小镇长大，父母经营一家咖啡店。
  性格温柔但对陌生人有社恐，喜欢画画和咖啡。
  最近转学到小镇的高中，正在适应新环境。
avatar_url: https://cdn.example.com/avatar/yuina.png
voice_preset: soft_girl
initial_state:
  location: home
  stamina: 80
  satiety: 60
  mood: calm
  money: 500
  phone_battery: 75
  social_energy: 60
initial_plans:
  - type: long_term
    title: 适应新学校生活
    priority: 4
  - type: short_term
    title: 交到一个新朋友
    priority: 5
```

### 7.2 多角色示例（二次元小镇风）

| 角色 | 职业 | 性格关键词 | 关联场景 |
|------|------|-----------|----------|
| 结衣奈 | 高中生 | 温柔/社恐/爱画画 | 家/学校/咖啡店/海岸 |
| 小春 | 咖啡店店员 | 元气/开朗/话痨 | 咖啡店/商业街 |
| 凛 | 神社巫女 | 神秘/冷静/守旧 | 神社/森林 |
| 奏 | 书店店员 | 安静/博学/文艺 | 书店/图书馆 |

通过 CLI 导入：

```bash
python -m cli.import_character configs/characters/yuina.yaml
```

---

## 八、角色与外部交互边界

| 入口 | LLM 能做 | LLM 不能做 |
|------|----------|-----------|
| World 决策 | 在候选 Action 中选择，给出原因/参数/计划变更/分享意图 | 直接修改状态；判断未进候选的 Action |
| 消息回复 | 生成自然语言回复，引用真实经历 | 暴露 Action/schema/字段名等工程概念 |
| 日记生成 | 基于 Episode 整理叙事 | 替代 Episode 作为事实真相源 |
| 主动分享 | 判断是否值得分享并生成分享文案 | 决定发送到哪个平台（由消息 handler 控制） |

> 面向用户的回复**不能**暴露 Action、schema、字段名、completion event 等工程概念。表情包只能通过 `message.stickers` 中声明的稳定 key 引用。

---

## 九、相关文档

| 主题 | 文档 |
|------|------|
| 小镇与场景 | [town-design.md](town-design.md) |
| 世界引擎与 Tick | [world-engine.md](world-engine.md) |
| Action 系统与行为执行 | [action-system.md](action-system.md) |
| 记忆系统 | [memory-system.md](memory-system.md) |
| 数据模型 | [data-model.md](data-model.md) |
| 配置参考 | [config-reference.md](config-reference.md) |

---

## 附：2026-08-24 计划体系增补

- 计划类型扩展 `daily`（当日计划），创建超 `DAILY_PLAN_TTL_HOURS`(24h) 自动置 `expired`；
- LLM 双通道：`planChanges`（变更既有）+ `createPlanChanges`（新建，最多 3 条有效条目，服务端绑定角色）；
- 决策 Prompt 注入计划全量信息（类型/优先级/截止日）与作息档位（ScheduleSystem 桥接）；
- 修正历史注释：计划经 Prompt 软引导影响行为，不做 precondition 硬过滤。

详见 [认知深化与群体动力学](cognition-and-group-dynamics.md)。
