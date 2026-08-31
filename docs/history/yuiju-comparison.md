# aitown vs yuiju 业务设计对比

> 本文档对比 aitown 与 yuiju 两个项目在角色驱动的虚拟生活模拟领域的业务设计差异，并给出 aitown 可借鉴的具体改进点。
>
> 对比维度：角色提示词约束、世界模型、角色设计、记忆系统、LLM 工具系统、配置管理。
>
> 核心定位差异：
>
> - **yuiju**：单角色（悠酱）深度陪伴型 AI，强调人格细腻度、跨世界边界、长期生活感。Prompt 内嵌于 TypeScript 代码，强调类型安全与运行时约束。
> - **aitown**：多角色虚拟小镇，强调世界推进、多角色共存、可观测性与可扩展性。Prompt 外置为 YAML，强调配置化与多角色复用。
>
> 更新日期：2026-07-13（标注已完成借鉴项 ✅）

---

## 一、角色提示词约束对比

| 维度           | yuiju                                                                                                   | aitown                                                                                            | 状态               |
| -------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------ |
| 提示词存放位置 | TypeScript 源码内嵌（`packages/utils/src/prompt/*.ts`）                                                 | YAML 外置（`configs/prompts/*.yaml`）                                                             | ✅ aitown 路线正确 |
| 角色身份       | 硬编码单角色 ゆいじゅ/悠酱                                                                              | 多角色，`{name}`/`{personality}` 模板变量                                                         | ✅ aitown 更优     |
| 人格分层       | 分 5 层：客观设定 / 聊天人格 / 状态表达 / 金币隐私 / 决策版人设                                         | ✅ 已实现分层：`[角色档案]` + `[聊天人格]` + `[状态表达规则]` + `[世界边界]` + `[真实感原则]`     | ✅ 已借鉴          |
| 说话风格约束   | 极细腻：说话温度、卡顿规则、颜文字原则、寂寞底色、轻文学感、被温柔碰到的反应                            | ✅ 已在 `[聊天人格]` 纳入：说话温度、卡顿规则、颜文字原则、寂寞底色、轻文学感                     | ✅ 已借鉴          |
| 场景化人设     | 聊天与决策使用不同人设片段（`characterPersonalityPrompt` vs `characterDecisionPrompt`）                 | ✅ 聊天用 `chat.yaml` 的 `[聊天人格]`，决策用 `decision.yaml` 的 `[社交决策提示]`，人设片段已分离 | ✅ 已借鉴          |
| 状态泄露防护   | 显式约束：数值不外泄（`characterStateExpressionPrompt`）、金币模糊表达（`characterMoneyPrivacyPrompt`） | ✅ 已在 `chat.yaml` 增加 `[状态表达规则]` 段：数值不外泄、用自然口语表达感受                      | ✅ 已借鉴          |
| 跨世界边界     | 显式声明：用户与角色不在同物理世界，禁止编造共同物理行动                                                | ✅ 已在 `chat.yaml` 增加 `[世界边界]` 段：明确角色与用户不在同物理世界                            | ✅ 已借鉴          |
| 工具使用规则   | 内嵌于角色卡：回忆走 `todayEventSearch`/`diarySearch`，工具返回为客观事实                               | 未在 Prompt 中声明工具使用规则                                                                    | ❌ 待借鉴          |
| 关系与边界     | 详尽：普通请求 vs 冒犯的区分、拒绝方式、被温柔碰到时的反应模式                                          | 部分实现：`[聊天人格]` 含"被温柔碰到的反应"约束                                                   | ⚠️ 部分借鉴        |
| 真实感原则     | 显式三条：真实比圆滑重要 / 克制比表演重要 / 主体性不能丢                                                | ✅ 已在 `chat.yaml` `[真实感原则]` 段完整纳入三原则                                               | ✅ 已借鉴          |

### aitown 可借鉴的改进点（更新状态）

1. ✅ **人设分层**：`chat.yaml` 已拆分为 `[角色档案]`（客观设定）+ `[聊天人格]`（聊天专用）两层。决策 Prompt 使用 `decision.yaml` 独立的人设片段，避免被聊天语气污染。
2. ✅ **状态泄露防护**：`chat.yaml` 已增加 `[状态表达规则]` 段，约束角色不得播报 stamina/satiety/mood 具体数值，仅用自然口语表达感受。
3. ✅ **场景化人设片段**：`decision.yaml` 已增加 `[社交决策提示]` 段，描述角色在社交场景的取舍原则，与聊天人格解耦。
4. ✅ **跨世界边界声明**：`chat.yaml` 已增加 `[世界边界]` 段，明确角色不得声称与用户共同进行物理行动。
5. ❌ **工具使用规则内嵌**：aitown 已有本地工具（`src/tools/`）检索记忆/知识，应在 Prompt 中显式声明「回忆走检索工具，工具返回为客观事实」。尚未实现。
6. ✅ **真实感三原则**：`chat.yaml` `[真实感原则]` 段已完整纳入（真实 > 圆滑、克制 > 表演、保留主体性）。

---

## 二、世界模型设计对比

| 维度       | yuiju                                                                               | aitown                                                             |
| ---------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Tick 间隔  | 固定 60 秒（`WORLD_TICK_INTERVAL_MS = 60_000`）                                     | 可配置（默认 N 秒）                                                |
| 运行模型   | 单实例 `setInterval`，`activeTick` 互斥锁防重入                                     | Redis 分布式锁选主（`LOCK_KEY = "world:tick:leader"`），多实例容错 |
| 状态存储   | 文件 / MongoDB（`worldState.getData()`）                                            | Redis Hash `world:state`（实时真相源）+ PG（历史快照）             |
| 演化链     | `WeatherEvolution` / `SceneEvolution` / `ResourceEvolution`，precondition → advance | `default_evolutions()`，含时间/天气/场景/资源/事件演化             |
| 命令系统   | `WorldCommand` 队列，Tick 时消费（资源消耗类）                                      | Action executor 显式执行，无独立命令队列                           |
| 时间恢复   | `recoverToNow()` 直接跑到当前时间                                                   | 从 Redis 恢复 `tick_id`，事件去重 + 快照闭环冷启动                 |
| 持久化粒度 | 每次 Tick 写完整状态                                                                | 仅状态变化时写 `world_events`（差分），每 1000 Tick 写完整快照     |
| 可观测性   | `logger.error` 简单日志                                                             | structlog + OTel Span + Prometheus 指标 + Langfuse                 |
| 容错       | 锁 TTL 自动过期                                                                     | 锁续租 + 监控                                                      |

### aitown 可借鉴的改进点

1. **命令队列模式**：yuiju 的 `enqueueCommand` + Tick 时统一消费模式，适合处理「角色购物消耗城镇资源」这类需要与世界状态原子合并的操作。aitown 当前通过 Action executor 直接写，可考虑为跨角色资源竞争场景引入命令队列。
2. **演化 precondition 显式化**：yuiju 每个 Evolution 都有 `precondition(context)` 判断是否需要推进，避免无意义计算。aitown 的演化应统一暴露 precondition 接口。
3. **时间恢复机制**：yuiju 的 `recoverToNow()` 思路简洁，aitown 已用快照+差分事件做得更完善，但可借鉴其「直接跑到当前时间」的语义清晰性。

---

## 三、角色设计对比

| 维度       | yuiju                                                                                 | aitown                                                                                   |
| ---------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 角色数量   | 单角色（单例 `CharacterState.getInstance()`）                                         | 多角色（PG `characters` 表 + Redis `char:{id}:state`）                                   |
| 状态封装   | 类方法封装（`setStamina`/`changeStamina`/`addItem`/`consumeItem`）                    | Redis Hash + Action executor 显式变更                                                    |
| 状态字段   | stamina/satiety/mood/money/phoneBattery/inventory/dailyActionsDoneToday/runningAction | location/stamina/satiety/mood/money/inventory/current_action/phone_battery/social_energy |
| 独有字段   | `dailyActionsDoneToday`（每日行为去重）/ `runningAction`（进行中行为）                | `location`（场景）/ `social_energy`（社交能量，社恐角色消耗更快）                        |
| 数值约束   | 类内 `Math.min(MAX, Math.max(0, x))` 显式 clamp                                       | `clamp_resource(value, 0, 100)` 工具函数                                                 |
| 背包系统   | 完整：`addItem`/`consumeItem`/`getItemQuantity`，含 quantity 合并                     | `inventory` jsonb，由 Action executor 维护                                               |
| 状态真相源 | 文件/MongoDB                                                                          | Redis（实时）+ PG `character_states`（镜像）                                             |
| 角色卡     | 硬编码 TypeScript                                                                     | YAML 配置文件（`configs/characters/*.yaml`）                                             |
| 性格影响   | 内嵌于人格 Prompt                                                                     | 性格标签进入决策 Prompt + `traits.schedule` 接入作息系统                                 |

### aitown 可借鉴的改进点

1. **每日行为去重**：yuiju 的 `dailyActionsDoneToday` 字段防止角色重复执行「每日一次」类行为（如神社参拜）。aitown 可在 `character_states` 增加类似字段，配合 Action precondition 实现。
2. **进行中行为状态**：yuiju 的 `runningAction` 显式记录当前进行中的行为（含开始/结束时间），aitown 的 `current_action` 已有类似设计，可补全 `started_at`/`ends_at` 字段以支持中断与恢复。
3. **状态访问封装**：yuiju 用类方法封装状态访问（`changeStamina(delta)`），aitown 直接操作 Redis Hash。aitown 可考虑在 `core/` 层提供 `CharacterStateAccessor` 封装常用变更，减少散落的 Redis 调用。

---

## 四、记忆系统设计对比

| 维度     | yuiju                                                                        | aitown                                                          |
| -------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------- |
| 记忆分层 | 人物记忆（person-memory）+ 今日事件（todayEventSearch）+ 日记（diarySearch） | MemoryEpisode（经历事实）+ 派生记忆（人物/计划/反思）+ 分层日记 |
| 记忆热度 | `person-memory-heat.json`：按 nickname 累计交互次数 + `lastInteractedAt`     | 未实现热度机制                                                  |
| 向量检索 | 未明确（依赖 `memory-search` 工具）                                          | pgvector 向量索引，按语义相似度 + 重要性 + 时间衰减排序         |
| 记忆来源 | 对话沉淀 + 行为沉淀                                                          | Action 执行 + 对话窗口 + 世界事件                               |
| 反思机制 | 未在阅读范围内明确                                                           | 累计 N 条未反思记忆触发，从多条 Episode 归纳高层认知            |
| 日记分层 | diarySearch 工具检索                                                         | 当日详情 / 周期摘要 / 长期归档三层                              |
| 存储格式 | JSON 文件（按 nickname 分文件）                                              | PG `memory_episodes` + pgvector                                 |
| 校验机制 | zod schema 严格校验 + `PersonMemoryFormatError`                              | Pydantic 模型校验                                               |
| 初始化   | `initializePersonMemoryHeat` 扫描目录补全缺失热度记录                        | 由 Action/对话自然沉淀                                          |

### aitown 可借鉴的改进点

1. **记忆热度机制**：yuiju 的 `person-memory-heat` 用交互次数衡量人物重要性，可决定哪些人物记忆优先检索。aitown 可在 `relations` 表增加 `interaction_count` 字段，或在派生记忆中引入热度衰减。
2. **今日事件 vs 历史日记分离**：yuiju 显式区分「今天发生的事」与「过去日记」，对应不同检索工具。aitown 可在检索服务中按时间窗口分流：当日 Episode 走快速检索，历史走向量检索。
3. **记忆文件初始化扫描**：yuiju 的初始化逻辑会扫描已有记忆文件并补全缺失的热度记录，这种「自愈」思路值得 aitown 在冷启动恢复时借鉴。

---

## 五、LLM 工具系统对比

| 维度         | yuiju                                                                                                                               | aitown                                                   |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| 工具注册     | 独立模块导出（`memory-search`/`person-memory`/`propose-plan-changes` 等）                                                           | Action 注册表（`registry.register`）                     |
| 工具分类     | 检索类（memory-search/person-memory/query-state/query-static-guide）+ 提案类（propose-plan-changes）+ 审查类（review-plan-changes） | Action 按 category 分类（MOVE/LIFE/WORK/SOCIAL/SPECIAL） |
| 状态修改权   | LLM 仅能提案（`proposePlanChanges`），需 `reviewPlanChanges` 审查后才生效                                                           | LLM 在候选 Action 中选择，executor 计算状态变更          |
| 候选过滤     | 未显式暴露 precondition 机制                                                                                                        | `precondition: (state) -> bool` 由代码过滤候选           |
| 工具调用约束 | Prompt 内显式声明：`proposePlanChanges` 只能调用一次、不代表已生效                                                                  | DecisionResult 结构化输出，`plan_changes` 仅作建议       |
| 静态知识查询 | `queryStaticGuide` 查询 worldMap/shopProducts/dinerMenu 等                                                                          | 世界地图注入 Prompt，未提供工具查询                      |
| 库存查询     | `query-available-inventory-items` 工具                                                                                              | 背包状态直接在 Redis 读取                                |

### aitown 可借鉴的改进点

1. **计划变更审查机制**：yuiju 的 `reviewPlanChanges` 工具强制 LLM 在提交计划变更前先自我审查，aitown 可在 `plan_changes` 写入前增加类似审查步骤，避免 LLM 频繁重写计划。
2. **静态知识工具化**：yuiju 用 `queryStaticGuide` 让 LLM 主动查询世界地图/商店菜单，而非全部塞进 Prompt。aitown 当前将世界地图直接注入决策 Prompt，当场景增多时会膨胀，可考虑改为工具按需查询。
3. **工具调用次数约束**：yuiju 在 Prompt 中显式声明「`proposePlanChanges` 只能调用一次」，aitown 应在 Prompt 中声明类似约束，防止 LLM 滥用工具。
4. **提案与生效分离**：yuiju 明确「`proposePlanChanges` 只表示提案已提交后台审查，不代表计划已更新成功」，aitown 的 `plan_changes` 设计已遵循此原则，但应在 Prompt 中显式告知 LLM 这一边界。

---

## 六、配置管理对比

| 维度         | yuiju                                            | aitown                                       |
| ------------ | ------------------------------------------------ | -------------------------------------------- |
| 配置格式     | TypeScript 单文件（`yuiju.config.ts.example`）   | `.env` + YAML 多文件（`configs/`）           |
| 类型安全     | `defineYuijuConfig` 函数 + zod schema 运行时校验 | pydantic-settings + Pydantic 模型            |
| LLM 模型分档 | 四档：chat/strong/flash/vision，每档可配多源     | 单一 LLM 客户端配置                          |
| 消息平台     | 内嵌 OneBot + Lark 配置（协议/重试/白名单）      | adapters 层（`onebot.py`/`lark.py`）独立配置 |
| 表情包       | 配置文件内联 stickers（key + uri + description） | 未在配置中明确                               |
| 时区         | `app.timezone: "Asia/Shanghai"`                  | 由 `.env` 配置                               |
| 记忆目录     | `app.memoryDir` 显式配置                         | 由 PG + pgvector 管理                        |
| 部署模式     | `app.publicDeployment` 标志                      | 由 docker-compose 管理                       |

### aitown 可借鉴的改进点

1. **LLM 模型分档**：yuiju 的 chat/strong/flash/vision 四档模型配置非常实用——日常聊天用 cheap 模型，重要决策用 strong 模型，图片理解用 vision 模型。aitown 当前单一 LLM 客户端会在成本上吃亏，建议引入分档配置。
2. **表情包配置化**：yuiju 将表情包 key/uri/description 集中配置，aitown 可在 `configs/` 增加类似配置，让角色卡引用稳定 key。
3. **重试策略配置化**：yuiju 的 OneBot/Lark 配置含 `retryTimes`/`retryInterval`/`retryLazy`/`responseTimeout`，aitown 的 adapters 层应将这些参数外置为配置。
4. **白名单机制**：yuiju 的 `whiteList`/`ownerList`/`groupWhiteList` 控制消息来源权限，aitown 在多租户场景下需要类似机制。

---

## 七、aitown 可借鉴的改进点（汇总，2026-07-13 更新）

按优先级排序，结合 aitown 现有架构落地：

### P0：立即借鉴

| 改进点                                       | 落地位置                          | 预期收益                                   | 状态      |
| -------------------------------------------- | --------------------------------- | ------------------------------------------ | --------- |
| 人设分层（客观设定 vs 聊天人格 vs 决策人设） | `configs/prompts/` 拆分片段       | 决策 Prompt 不被聊天语气污染，决策质量提升 | ✅ 已完成 |
| 状态泄露防护约束                             | `chat.yaml` 增加 `[状态表达规则]` | 角色不再向用户播报数值，沉浸感提升         | ✅ 已完成 |
| 真实感三原则                                 | `chat.yaml` `[真实感原则]` 段     | 降低模板腔概率                             | ✅ 已完成 |
| LLM 模型分档                                 | `config.py` + `LLMClient`         | 成本显著下降                               | ❌ 未完成 |

### P1：短期借鉴

| 改进点           | 落地位置                                          | 预期收益                         | 状态      |
| ---------------- | ------------------------------------------------- | -------------------------------- | --------- |
| 跨世界边界声明   | `chat.yaml` 增加 `[世界边界]` 段                  | 避免角色编造与用户的物理共同行动 | ✅ 已完成 |
| 场景化人设片段   | `decision.yaml` 增加 `[社交决策提示]` 段          | 决策更贴合角色个性               | ✅ 已完成 |
| 计划变更审查机制 | `plan_repo.py` + 决策 Prompt                      | 减少 LLM 频繁重写计划            | ❌ 未完成 |
| 记忆热度机制     | `relations` 表增加 `interaction_count`            | 人物记忆检索更精准               | ❌ 未完成 |
| 每日行为去重     | `character_states` 增加 `daily_actions_done` 字段 | 防止重复执行每日一次类行为       | ❌ 未完成 |

### P2：中期借鉴

| 改进点           | 落地位置                        | 预期收益                         | 状态                                 |
| ---------------- | ------------------------------- | -------------------------------- | ------------------------------------ |
| 静态知识工具化   | 本地工具 `knowledge.query_kb`   | 决策 Prompt 不再膨胀             | ✅ 已完成（2026-07-15 转为本地工具） |
| 工具使用规则内嵌 | Prompt 中声明工具返回为客观事实 | LLM 正确处理工具检索结果         | ❌ 未完成                            |
| 表情包配置化     | `configs/stickers.yaml`         | 表情包可配置，角色卡引用稳定 key | ❌ 未完成                            |
| 命令队列模式     | `core/world_engine.py`          | 跨角色资源竞争原子化             | ❌ 未完成                            |

### 新增：aitown 已超越 yuiju 的领域

| 领域         | aitown 实现                                                                     | yuiju 现状       |
| ------------ | ------------------------------------------------------------------------------- | ---------------- |
| 多智能体交互 | `chat_with` Action：场景感知 → LLM 对话生成 → 双向关系更新 → 双记忆持久化       | 单角色，无此需求 |
| 前端可见性   | Nearby Characters API + UI 展示同场景角色                                       | 无前端           |
| 关系图演化   | RelationGraph 自动升级（stranger→acquaintance→friend→close_friend→best_friend） | 无关系图         |

### 借鉴时的注意事项

1. **单角色 vs 多角色**：yuiju 的许多细腻约束（如说话温度、被温柔碰到的反应）是为单角色深度定制的，aitown 多角色场景下应提取共性规则，避免每个角色卡都写一遍。
2. **Prompt 膨胀风险**：yuiju 的角色卡 Prompt 极长（接近 200 行），aitown 引入时需注意 Token 成本，建议将稳定约束放 system prompt，动态状态放 user prompt。
3. **配置真相源**：yuiju 用 TypeScript 单文件保证类型安全，aitown 用 YAML + Pydantic 保证校验，两者都符合「单一真相源」原则，aitown 应坚持 YAML 外置路线，不要退化为代码内嵌。

---

## 相关文档

| 主题               | 文档                                                           |
| ------------------ | -------------------------------------------------------------- |
| aitown 角色设计    | [character-design.md](character-design.md)                     |
| aitown 小镇设计    | [town-design.md](town-design.md)                               |
| aitown 世界引擎    | [world-engine.md](world-engine.md)                             |
| aitown Action 系统 | [action-system.md](action-system.md)                           |
| aitown 记忆系统    | [memory-system.md](memory-system.md)                           |
| aitown 配置参考    | [config-reference.md](config-reference.md)                     |
| 代码风格规范       | [rules/implementation-style.md](rules/implementation-style.md) |
| Prompt 规范        | [rules/prompt-style.md](rules/prompt-style.md)                 |
