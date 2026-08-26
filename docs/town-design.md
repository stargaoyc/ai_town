# 小镇设计

> 本文档定义 AI Town 中"虚拟小镇"的完整设计：地理概念、场景清单、场景属性、移动矩阵、资源系统、节日与事件。
>
> 小镇是角色生活的舞台。设计目标：让场景有真实的开放节奏与拥挤度，让移动有可感知的时间成本，让节日与天气成为生活的一部分。

---

## 一、设计原则

| 原则 | 说明 |
|------|------|
| 世界地图集中维护 | 场景与连通关系集中定义在 `configs/world-map.yaml`，并同步到决策 Prompt |
| 场景有生命 | 场景有营业时间、容量上限、动态拥挤度，不是静态背景板 |
| 移动有成本 | 场景间移动消耗时间与体力，影响行为决策 |
| 室内外区分 | 室内场景不受天气影响，室外场景受天气调节 |
| 节日驱动 | 节日/事件改变场景状态与角色可选行为 |

> 真相源约定：场景静态定义在 `configs/world-map.yaml`，实时态（拥挤度/开放状态）存 Redis `world:state`。

---

## 二、小镇地理概念

### 2.1 世界地图（World Map）

小镇采用**节点-边**模型：每个场景是一个节点，场景间的连通关系是边，边权重为移动耗时（分钟）。

```text
                    ┌──────────┐
                    │  神社     │
                    │ shrine   │
                    └────┬─────┘
                         │ 10min
        ┌────────────────┼────────────────┐
        │10min           │                │
   ┌────▼─────┐    ┌─────▼──────┐   ┌─────▼─────┐
   │ 学校      │───►│  中央广场   │◄──│  森林      │
   │ school   │15min│ plaza      │15min│ forest   │
   └────┬─────┘    └─────┬──────┘   └───────────┘
        │5min             │5min
   ┌────▼─────┐    ┌─────▼──────┐   ┌───────────┐
   │ 家        │◄──►│  商业街     │──►│  海岸      │
   │ home     │10min│ shopping_  │10min│ coast    │
   └──────────┘    │  street    │   └───────────┘
                   └─────┬──────┘
                         │3min
              ┌──────────┼──────────┐
              │          │          │
        ┌─────▼────┐ ┌───▼────┐ ┌───▼──────┐
        │ 咖啡店    │ │ 书店    │ │ 便利店    │
        │ cafe     │ │bookstore│ │convenience│
        └──────────┘ └────────┘ └──────────┘
```

### 2.2 场景连通矩阵（移动耗时，单位：虚拟分钟）

| from \ to | home | school | cafe | bookstore | park | shrine | coast | library | plaza | shopping_street |
|-----------|------|--------|------|-----------|------|--------|-------|---------|-------|-----------------|
| home | 0 | 5 | 8 | 8 | 10 | 10 | 12 | 7 | 6 | 10 |
| school | 5 | 0 | 6 | 5 | 8 | 12 | 14 | 4 | 5 | 7 |
| cafe | 8 | 6 | 0 | 3 | 7 | 12 | 12 | 5 | 3 | 3 |
| plaza | 6 | 5 | 3 | 3 | 4 | 10 | 8 | 4 | 0 | 4 |

> 完整矩阵在 `configs/world-map.yaml` 维护。移动 Action 根据 `current_location → target` 查表得到 `duration_minutes`。

---

## 三、场景清单（二次元小镇风）

### 3.1 场景属性 schema

```yaml
scenes:
  - id: cafe                       # 唯一标识
    name: 咖啡店
    type: indoor                    # indoor | outdoor
    open_hours: [7, 22]             # 营业时间（0-24）
    capacity: 20                    # 最大容量
    activities: [eat, drink, work_parttime, chat, relax]
    workday_only: false
    weather_affected: false         # 室内不受天气影响
    description: "飘着咖啡香气的小店，结衣奈父母经营。"
```

### 3.2 场景清单

| 场景 | id | 类型 | 营业时间 | 容量 | 主要活动 | 备注 |
|------|----|------|----------|------|----------|------|
| 家 | `home` | indoor | 全天 | 5 | sleep/eat/relax/study | 角色住所，私有 |
| 学校 | `school` | indoor | 8–17 | 50 | study/chat/exercise | 工作日开放 |
| 咖啡店 | `cafe` | indoor | 7–22 | 20 | eat/drink/work_parttime/chat | 可打工 |
| 书店 | `bookstore` | indoor | 9–21 | 15 | read/buy/work_parttime | 可打工 |
| 图书馆 | `library` | indoor | 9–20 | 30 | study/read/borrow | 安静，禁大声 |
| 公园 | `park` | outdoor | 全天 | 100 | relax/chat/exercise | 受天气影响 |
| 神社 | `shrine` | outdoor | 全天 | 40 | pray/relax/festival | 节日主场 |
| 海岸 | `coast` | outdoor | 全天 | 80 | relax/walk/look_at_sea | 受天气影响 |
| 森林 | `forest` | outdoor | 全天 | 30 | explore/relax/forage | 受天气影响 |
| 中央广场 | `plaza` | outdoor | 全天 | 60 | chat/relax/event | 节日集会点 |
| 商业街 | `shopping_street` | outdoor | 10–22 | 80 | shop/eat/chat | 综合消费 |
| 便利店 | `convenience_store` | indoor | 全天 | 10 | buy/eat | 24h |

### 3.3 场景动态状态

实时态存 Redis `world:state.locations`，由 World Tick 更新：

```text
locations:
  cafe:    { open: 1, crowdedness: 23, visitors: [uuid, uuid] }
  school:  { open: 1, crowdedness: 68, visitors: [...] }
  park:    { open: 1, crowdedness: 5,  visitors: [] }
```

| 字段 | 说明 | 更新规则 |
|------|------|----------|
| `open` | 是否开放 | 由 `open_hours` + `workday_only` 计算 |
| `crowdedness` | 拥挤度 0–100 | 由 `len(visitors) / capacity * 100` 计算，叠加随机波动 |
| `visitors` | 当前在场角色 id 列表 | Action 执行时实时维护 |

---

## 四、场景与行为的关系

### 4.1 活动绑定

每个场景声明它支持的 `activities`，Action 注册时绑定 `scene + activity`：

```python
Action(
    id="work_parttime_cafe",
    name="在咖啡店打工",
    category=ActionCategory.WORK,
    scene="cafe",                    # 必须在咖啡店
    activity="work_parttime",        # 对应场景声明
    precondition=lambda s: (
        s.location == "cafe"
        and s.world.cafe.open
        and has_shift(s)             # 有排班
    ),
    executor=lambda s, p: s.replace(money=s.money + 10, stamina=s.stamina - 8),
    duration_minutes=60,
)
```

### 4.2 拥挤度影响

拥挤度会进入决策 Prompt，并影响部分行为：

| 场景状态 | 影响 |
|----------|------|
| `crowdedness > 80` | 社恐角色倾向离开；社交类 Action 候选增加 |
| `crowdedness < 20` | 适合学习/放松类 Action |
| `open == 0` | 该场景所有 Action 被前置条件过滤 |

---

## 五、移动系统

### 5.1 移动 Action

移动是一类特殊 Action，由 `current_location → target` 动态生成：

```python
def register_move_actions(registry, world_map):
    for src, dsts in world_map.adjacency.items():
        for dst, minutes in dsts.items():
            registry.register(Action(
                id=f"move_{src}_to_{dst}",
                name=f"从{world_map.name(src)}前往{world_map.name(dst)}",
                category=ActionCategory.MOVE,
                precondition=lambda s, src=src: s.location == src,
                executor=lambda s, p, dst=dst: s.replace(location=dst),
                duration_minutes=minutes,
                energy_cost=-2,              # 移动消耗体力
            ))
```

### 5.2 移动决策

LLM 在候选 Action 中看到所有可达场景的移动选项，描述格式：

```text
3. move_home_to_school: 从家前往学校。[体力-2][耗时5分钟]
4. move_home_to_cafe: 从家前往咖啡店。[体力-2][耗时8分钟]
```

> 移动耗时支持**动态调整**：恶劣天气下室外移动耗时 ×1.5，详见 [世界引擎 - 动态耗时](world-engine.md#动态耗时系统)。

---

## 六、小镇资源系统

### 6.1 全局资源

存于 Redis `world:state.resources`，由 World Tick 周期性更新：

| 资源 | 说明 | 影响场景 |
|------|------|----------|
| `food` | 城镇食物供给 0–100 | 便利店/咖啡店食物可购买量 |
| `energy` | 城镇能源 0–100 | 室内场景开放（< 20 时部分场景关闭） |
| `goods` | 商品种类 0–100 | 商业街购物多样性 |

商品清单与基础库存/价格/消耗量由 `configs/resources.yaml` 定义（ResourceEvolution 启动加载，缺失即启动失败），详见 [配置参考](config-reference.md#六场景与资源配置)。

### 6.2 资源循环

```text
资源消耗（角色购物/用餐） → 资源下降
     ↓
World Tick 资源补充（模拟补给） → 资源回升
     ↓
资源过低 → 场景物价上涨 / 部分商品缺货（进入 Prompt）
```

---

## 七、节日与事件系统

### 7.1 节日配置

```yaml
# configs/events.yaml
events:
  - id: sakura_festival
    name: 樱花祭
    date: "04-05"                    # 每年 4 月 5 日（虚拟历）
    duration_days: 3
    main_scenes: [shrine, plaza]
    activities: [festival_stall, watch_fireworks, pray, chat]
    weather_preference: sunny
    description: "小镇一年一度的樱花祭，神社会举办祭典。"
  - id: summer_festival
    name: 夏日祭
    date: "08-15"
    duration_days: 1
    main_scenes: [shrine, coast]
    activities: [watch_fireworks, eat_stall_food, wear_yukata]
  - id: cultural_festival
    name: 文化祭
    date: "11-03"
    duration_days: 2
    main_scenes: [school]
    activities: [exhibit, performance, chat]
```

### 7.2 事件触发

World Tick 的 `check_events()` 按虚拟日期检查：

- 触发节日 → 修改场景 `activities`（追加节日活动）→ 广播 `WORLD_EVENT_BROADCAST`
- 节日主场场景 `crowdedness` 显著上升
- 角色在感知阶段收到事件广播，注入决策上下文

### 7.3 突发事件

支持随机突发事件（不影响主线，仅增加生活感）：

| 事件 | 影响 |
|------|------|
| 突降大雨 | 室外场景 `crowdedness` 暴跌；移动耗时 ×1.5 |
| 咖啡店新品 | 咖啡店 `crowdedness` 上升；触发尝鲜 Action |
| 海边出现海豚 | 海岸 `crowdedness` 上升；触发观海 Action |

---

## 八、天气与场景联动

| 天气 | 室外场景影响 | 行为影响 |
|------|--------------|----------|
| sunny | `crowdedness +10` | 户外放松/运动 Action 加分 |
| cloudy | 无 | 正常 |
| rainy | `crowdedness -30` | 室内场景拥挤度上升；移动耗时 ×1.5 |
| snowy | `crowdedness -40` | 移动耗时 ×1.5；触发打雪仗 Action |
| windy | `crowdedness -10` | 海岸/森林 Action 减分 |

> 室内场景 `weather_affected: false`，不受上述影响。

---

## 九、世界地图 Prompt 维护

世界地图是决策 Prompt 的重要组成部分，集中维护避免漂移：

```text
[世界地图]
家(home) — 学校(school, 5min) — 咖啡店(cafe, 8min)
家(home) — 商业街(shopping_street, 10min) — 海岸(coast, 10min)
商业街(shopping_street) — 咖啡店(cafe, 3min) — 书店(bookstore, 3min)
中央广场(plaza) — 神社(shrine, 10min) / 森林(forest, 15min) / 海岸(coast, 8min)

[当前场景]
位置: 咖啡店(cafe)
开放: 是 | 拥挤度: 23/100 | 在场: 2人
可活动: eat / drink / work_parttime / chat / relax
```

> 修改场景或连通关系时，必须同步更新 `configs/world-map.yaml` 与决策 Prompt 中的地图描述。

---

## 十、相关文档

| 主题 | 文档 |
|------|------|
| 角色设计 | [character-design.md](character-design.md) |
| 世界引擎 | [world-engine.md](world-engine.md) |
| Action 系统 | [action-system.md](action-system.md) |
| 数据模型 | [data-model.md](data-model.md) |
| 配置参考 | [config-reference.md](config-reference.md) |
