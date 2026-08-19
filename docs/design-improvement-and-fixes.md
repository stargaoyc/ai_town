# AI Town 设计改进与问题修复文档

> 审查日期：2026-08-19
> 审查范围：`packages/backend/src`（124 个 Python 文件）、`packages/frontend/src`、`configs/`、`docker-compose*.yml`、`docker/`、`docs/`
> 严重程度定义：**P0** = 数据损坏 / 安全泄露 / 功能阻断；**P1** = 架构性缺陷 / 重要功能失效；**P2** = 坏味道 / 维护性问题

---

## 目录

1. [总体结论](#一总体结论)
2. [P0 致命问题（必须立即修复）](#二p0-致命问题)
3. [架构设计缺陷](#三架构设计缺陷)
4. [安全问题](#四安全问题)
5. [性能与稳定性问题](#五性能与稳定性问题)
6. [配置体系问题](#六配置体系问题)
7. [前端问题](#七前端问题)
8. [部署与工程化问题](#八部署与工程化问题)
9. [文档一致性问题](#九文档一致性问题)
10. [测试覆盖缺口](#十测试覆盖缺口)
11. [死代码清单](#十一死代码清单)
12. [修复路线图](#十二修复路线图)

---

## 一、总体结论

项目文档体系（23 篇设计文档 + AGENTS.md 规范）质量很高，设计原则（状态驱动、事实优先、闭环演化、单一真相源）清晰。**但实现与规范之间存在系统性脱节**：规范声明的核心契约（Redis/PG 双写顺序、PG 镜像回灌、LLM 不直接改状态、Prompt 外置、Action executor 模式）在关键路径上被违反或未落地。全库共发现 **P0 问题 11 个、P1 问题约 35 个、P2 问题约 45 个**。

最危险的三个系统性问题：

1. **状态一致性契约名存实亡**：「先 PG 事务再写 Redis」在全库只有 `tick.py` 的 `_execute_action` 一处真正执行；工具产生的金钱/库存变更完全不写 PG；「PG 镜像回灌」逻辑根本不存在。Redis 故障即数据永久丢失。
2. **安全防线多处洞开**：聊天记录、管理日志、运行时配置被列为公开 GET；WebSocket 完全无鉴权；RBAC 默认全员 admin；CORS 配置无效。
3. **LLM 成本控制只覆盖了最小的消耗路径**：每 30 秒一次的全体角色 Tick（系统最大 LLM 消耗方）完全绕过熔断器与每日预算。

---

## 二、P0 致命问题

### P0-1 工具产生的状态变更只写 Redis，PG 镜像永久落后

**位置**：`packages/backend/src/core/character/tick.py:625-734`（`_apply_tool_deltas`）

工具（shop.buy_item、give_gift 等）产生的 money/inventory/mood 变更最终只执行 `redis.hset`（tick.py:733-734），**完全没有 PG 写入**。角色的金钱、库存经工具变更后，PG 镜像永远停留在旧值，重启或 Redis 故障后状态静默回退。

**修复方案**：将工具 delta 合并进 `_execute_action` 的同一个 PG 事务（ActionRecord + update_state + CharacterStateHistory），commit 成功后再写 Redis，与现有双写点（tick.py:813-880）对齐。

### P0-2 Redis 状态序列化使用 `str(v)`，dict 字段被写成 Python repr，导致库存被静默清空

**位置**：`tick.py:877-880`

```python
await self.redis.hset(
    f"char:{character_id}:state",
    mapping={k: str(v) for k, v in new_state.items() if v is not None},
)
```

`str(dict)` 产生的是 Python repr（单引号），不是 JSON。下游 `_apply_tool_deltas`（tick.py:665-667）读回后 `isinstance(x, dict)` 为 False，**将角色库存静默重置为 `{}`**。且 `_apply_tool_deltas` 自己用 `json.dumps`（tick.py:678）写 inventory——同一字段两种序列化格式并存。另外 `world:state.world_time` 存在三种序列化（engine 的 `str()`、evolution 的 JSON、五处下游「兼容双重序列化」防御代码）。

**修复方案**：定义唯一的 Redis 状态编码器（标量直接 str，复合类型一律 JSON），放在 `src/db/` 或 `src/core/` 单点定义，删除所有下游「兼容解析」代码；为库存读写加回归测试。

### P0-3 PG→Redis 回灌逻辑不存在

**位置**：全库 grep `回灌|reconcile|backfill|rehydrate` 无匹配

规范声明「Redis 失败时由 PG 镜像回灌」，但没有任何启动时/运行时的恢复路径。唯一的间接恢复是 `_perceive` 在 Redis 哈希为空时惰性读 PG（tick.py:230-244），但工具写入从未进 PG（P0-1），回灌回来的也是旧值。

**修复方案**：在 lifespan 启动流程中增加 `rehydrate_states()`：扫描 PG `character_states` 与 `world_snapshots`，对 Redis 缺失/过期的键批量回灌；配合 P0-1 修复保证 PG 镜像完整。

### P0-4 角色 Tick 主循环串行执行，并发模型名存实亡

**位置**：`packages/backend/src/main.py:457-460`

```python
for char in characters:
    try:
        await character_engine.tick_character(char.id)
```

主循环串行 await 每个角色。`tick.py` 的 `asyncio.Semaphore` 并发控制和 `tick_all_active` 的 `asyncio.gather`（tick.py:1352-1353）全是死代码（`tick_all_active` 无调用方）。50 个角色 × 每个 Tick 含多次 LLM 调用，一轮可达数分钟，远超 `character_tick_seconds=30` 间隔；一个慢角色拖垮整个批次。

**修复方案**：主循环改用 `asyncio.gather` + 已有的 Semaphore 并发执行（复用 `tick_all_active` 并接入），同时修复 Semaphore 热更新不重建的问题（见 P1）。

### P0-5 LLM 熔断器与每日预算不覆盖 Tick 路径

**位置**：`packages/backend/src/messaging/service.py:563-573, 589-601`（唯一生效点）

成本控制只在用户消息路径生效。系统最大的 LLM 消耗方全部绕过：角色决策（tick.py:515）、chat_with（tick.py:1025）、反思（reflection_service.py:86）、记忆评分（episode_service.py:81）、分享文案（proactive_sharing.py:385,429）、日记（diary_service.py:122）、群聊判定（service.py:226）。LLM 故障时全体角色每 30 秒撞墙一次，日预算可被 Tick 流量打穿。

**修复方案**：在 `llm/client.py` 的 `chat()` / `structured_output()` 内部统一挂载预算检查 + 熔断（单一挂载点，而非各调用方手工接入）；激活已有的 `with_cost_control` 装饰器（cost_control/decorators.py:59-143，当前零调用）或删除它；对 Tick 路径的 circuit-open 行为定义为「跳过本角色本周期」。

### P0-6 Prompt 内嵌严重违反外置规范

**位置**（共 9 处内嵌 Prompt）：

- `tick.py:1003-1022`（chat_with 完整 Prompt）、`tick.py:465-488`（决策追加段）
- `messaging/service.py:208-224`（群聊判定）、`service.py:726-729`（上下文压缩）
- `proactive_sharing.py:369-382, 419-426`（分享文案）
- `memory/episode_service.py:63-78`（重要性评分）
- `memory/reflection_service.py:74-83`（反思——reflection.yaml 存在却不用）
- `memory/diary_service.py:109-119`（日记）
- `memory/person_memory_service.py:90-99`（Person Memory）
- `llm/prompts.py:17-24, 30-116`（SAFETY_SYSTEM_PROMPT 及三个内置兜底模板——其中 SAFETY_SYSTEM_PROMPT 与 `configs/prompts/chat.yaml:3-11` 内容重复，双真相源）

且 `prompts.py:172-180` 在 YAML 缺失时**静默回退**内置模板，YAML 被误删后系统继续用过期模板运行，无告警。

**修复方案**：全部外置到 `configs/prompts/*.yaml`；YAML 缺失时启动即报错（fail-fast），仅在开发模式允许回退并打 warning。

### P0-7 Action.executor 抽象从未被调用，move 无目标场景校验

**位置**：`actions/move.py:26-31` 定义了 `_move_executor`，但全库 grep `.executor(` 无调用。`tick.py:790-791` 用硬编码替代：

```python
if decision.action == "move" and decision.params.get("target_scene"):
    new_state["location"] = decision.params["target_scene"]
```

后果：LLM 幻觉一个不存在的 `target_scene`（如 `"mars"`）会被直接写入角色位置，下游 SceneEvolution、`get_characters_by_location` 全部带脏数据运行。`compute_move_duration`、`MovementSystem`、场景开放时间校验均未接进 Tick 主链路。

**修复方案**：执行层统一调用 `action.executor(state, params)` 并合并返回的 `new_state`，删除 tick.py 中的 action 特判硬编码；在 executor 或 precondition 中校验目标场景在 `scenes.yaml` 中存在且与当前场景连通。

### P0-8 WebSocket 无鉴权 + 管理接口列为公开 GET

**位置**：

- `messaging/websocket.py:263-301`：`/ws/chat/{character_id}` 无鉴权，`AuthMiddleware`（main.py:651-655）对非 http scope 直接透传。任何人可用任意 `user_id` 查询参数冒充用户对话并读取其历史。
- `adapters/onebot.py:372-378`：`/ws/onebot/v12` 无鉴权——任何人可冒充 OneBot 实现连入，注入伪造消息事件驱动 LLM 消费（烧预算）。
- `main.py:629-646`：`PUBLIC_GET_PREFIXES` 包含 `/api/v1/messages/history`（任意人可读任意用户私信）、`/api/v1/admin/logs`、`/api/v1/admin/config`、`/api/v1/admin/status` 等。

**修复方案**：WebSocket 握手阶段校验 JWT（query param 或 subprotocol）；OneBot 端点实现标准 access-token 校验；从 PUBLIC_GET 白名单移除所有 messages/admin 前缀，保留 health 等真正公开项；消息历史接口增加会话归属校验。

### P0-9 world-map.yaml 移动矩阵严重不完整

**位置**：`configs/world-map.yaml:4-55`（仅 5 个起点的出边）vs `configs/scenes.yaml`（12 个场景）

`bookstore`、`library`、`park`、`shrine`、`coast`、`forest`、`convenience_store` 共 7 个场景没有任何出发边；`forest` 从未作为目标出现（不可达孤岛）；矩阵不对称（`shopping_street → bookstore: 3` 无反向）。后端用 `DEFAULT_MOVE_DURATION = 10`（move.py:23,63）静默兜底，问题运行期不可见。

**修复方案**：补全 12×12 连通矩阵（或改为「相邻图 + 最短路计算」模型）；在 `modules/town/loader.py` 加载时做校验：所有场景必须互相可达、矩阵对称性检查，不满足则启动报错。

### P0-10 postgres 镜像构建依赖未追踪文件，fresh clone 必失败

**位置**：`docker/postgres/Dockerfile:4` `COPY pg_uuidv7-main.tar.gz`

`.gitignore:41` 的 `*.tar.gz` 将该文件忽略，`git ls-files` 确认未追踪。任何新环境 `docker compose build postgres` 必然失败。

**修复方案**：改为在 Dockerfile 中从官方 release 下载（`ADD https://github.com/...` + 校验和），或用 `git lfs` / 提交该文件；同时统一三份 compose 的 PG 大版本（见 P1-部署）。

### P0-11 双配置体系默认值不一致

**位置**：`config.py:60-76` vs `config_runtime.py:38-49`

同一配置项两套默认值：`share_cooldown_seconds`（1800 vs 300）、`share_probability_action`（0.6 vs 0.15）、`character_tick_seconds`（30 vs 60）、`llm_daily_budget_usd`（10.0 vs 5.0）等 9 项。`get_runtime_config()` 在 load 之前被调用时拿到 RuntimeConfig 自己的默认值——同一事实两个定义。

**修复方案**：RuntimeConfig 所有字段默认 `None`（未设置），读取时 fall back 到 `settings`；删除 RuntimeConfig 中的硬编码默认值，Settings 为唯一默认值真相源。

---

## 三、架构设计缺陷

### A-1 分层契约被 API 层穿透（P1）

- `api/characters.py:249-268`：`POST /characters/{id}/move` 在 API 层直接读写 Redis 真相源，且**只写 Redis 不写 PG 镜像**，位置字段双库分叉。
- `api/admin.py:118-137`：`force_world_tick` 直接调用引擎私有方法 `_execute_tick()`，**绕过 Leader Election**——多实例部署时非 Leader 实例调用此接口会双写世界状态。
- `api/admin.py:140-206`：`reset_world_time` 在 API 层编排领域逻辑（删哈希/写时间/算 day_phase/season）。
- `api/admin.py:646-683`：`GET /admin/logs` 在 API 层同步 `f.readlines()` 全量读日志文件到内存（async 端点中的阻塞 I/O）。

**修复**：新增 `CharacterService` / `WorldAdminService`，将上述逻辑下沉；force_world_tick 必须通过 Leader 检查或转发给 Leader。

### A-2 Redis/PG 双写顺序在多处被违反（P1）

- `tick.py:698-730`：工具的 `relation_strength_delta` 在独立 `db.session()` 写 PG，与 Redis 更新不在同一事务，中途失败即分叉。
- `modules/relation/graph.py:93-162`：Redis 缓存写入发生在 PG commit **之前**（顺序与规范相反），commit 失败则 Redis 脏数据。
- `tick.py:876-880`：PG commit 后 `redis.hset` 失败直接 raise——PG 已提交、Redis 未更新，**双库永久分叉且无对账机制**。

**修复**：引入「状态写入器」单点组件，封装「PG 事务 → Redis 写 → 失败时记 outbox 待对账」；增加定期对账任务（Redis vs PG diff → 告警/自动回灌）。

### A-3 runtime.py 与 main.py 双轨全局实例（P1）

`runtime.py:33-47` 是 15 个模块级可变全局 + 16 对 set/get（所有 getter 返回 `X | None`，迫使 30+ 处调用点重复 `if not x: raise 503`）；同时 `main.py:85-99` 保留了一套平行的模块级全局实例，lifespan 同时写两套——同一事实两处定义。另有 `_notif_key` 与 `create_notification` 在 runtime.py:222-257 与 `api/notifications.py:16-45` 完全重复实现。

**修复**：收敛为一个 `AppContext` 依赖容器（构造期注入，属性访问即非 None），删除 main.py 平行全局；改为 FastAPI `Depends` 注入；删除 notifications.py 重复实现。

### A-4 延迟 import 遍布全库，依赖方向未真正理顺（P2）

`tick.py` 内 12+ 处函数内 import（:179, :188, :293, :319, :408, :555, :784, :949, :1207, :1228, :1278），`websocket.py:258`、`onebot.py:119`、`tools/registry.py:174` 同样。说明循环依赖是被延迟导入掩盖而非解决。`main.py:74-80` 甚至对自己代码库内必然存在的模块做 `try: import / except ImportError` 降级（无意义防御）。

**修复**：画出模块依赖图，把被互相依赖的「通知推送、工具注册」等提取为独立契约模块（或事件总线），消除函数内 import。

### A-5 LLM 决策契约漏洞（P1）

- `tick.py:780`：`duration = decision.duration or action_def.duration_minutes` **未检查 `action_def.allow_dynamic_duration`**（base.py:55 定义了该字段但无人读取）——LLM 可对任何 Action 任意指定时长。
- `tick.py:793-799`：`_SOLO_RECOVERY_ACTIONS` 硬编码「relax/sleep/read_book 恢复社交能量 +10」——游离在 Action 定义之外的业务规则，同一行为效果的真相被拆到两处。
- `apply_cost_fields` 对 money 不 clamp 下界（base.py:118-119），而 `_apply_tool_deltas` 对 money `max(0, ...)`（tick.py:652）——同一字段两处下界处理不一致，money 可经 Action 路径变负。
- `api/actions.py:68-69`：`getattr(action, "preconditions", {})` —— Action 模型上不存在该属性，永远返回 `{}`，接口在撒谎。

**修复**：duration 合法性在执行层统一校验（`allow_dynamic_duration` + min/max 范围）；社交能量恢复并入 Action cost 字段体系；统一资源下界规则到单点函数；修正 actions API 返回真实字段。

### A-6 群聊智能回复判定路径的角色解析 bug（P1）

`adapters/onebot.py:664`：`_should_reply_in_group` 内调 `_resolve_character_id(is_group=True, group_id=None)`——**group_id 传了 None**，群-角色映射在判定路径永远失效（用默认角色），而真正回复时（onebot.py:525）又用带 group_id 的解析。判定角色与回复角色可能不是同一个。

### A-7 LLM 成本估算失真且双轨（P1）

`messaging/service.py:582-587`：`estimated_tokens = max(len(prompt)//3, len(response)//3)`、单价硬编码 `0.000001`。中文约 1.5 字/token，估算严重失真；`max()` 漏算一半；该失真值被持久化并计入预算。而 `llm/client.py:195-205` 已从 `response_metadata` 取到**真实** token_usage 并计入 Prometheus——真实值被丢弃，估算值被执行。单价还在 client.py:204/280/541 与 service.py 之间不一致。

**修复**：`chat()`/`structured_output()` 返回值统一携带真实 usage（structured_output 需改用 `with_structured_output(include_raw=True)` 或 Runnable 配置取 metadata）；预算与持久化一律使用真实值；单价表外置配置。

---

## 四、安全问题

### S-1 RBAC 默认全员 admin（P1）

`auth/jwt_handler.py:97-116`：`rbac_roles` 未配置（默认空串）时 `_resolve_role` 直接 `return "admin"`。「向后兼容」实质是把 RBAC 默认打成全开，`require_role("admin")` 在默认配置下形同虚设。另外 `api/admin.py:936-958` 的 `DELETE /admin/config/{key}` 漏配 `user: Admin` 依赖。

**修复**：默认角色改为 `viewer`（最小权限），admin 需显式配置；补齐所有 admin 端点的依赖审计。

### S-2 CORS 配置无效且危险（P1）

`main.py:607-613`：`allow_origins=["*"]` + `allow_credentials=True`——浏览器规范下该组合无效，实际等于放弃跨域防护。

**修复**：`allow_origins` 从配置读取具体域名列表，生产禁止 `*`。

### S-3 默认弱口令与弱默认值（P1）

`config.py:47`：`admin_password = "admin123"`；`docker-compose.infra.yml:10`：`POSTGRES_PASSWORD: password`；`docker-compose.yml:28,63,155`：`${DB_PASSWORD:-password}`、`${GRAFANA_PASSWORD:-admin123}`；`.env.example` 含 `JWT_SECRET=your-super-secret-key-change-in-production`。

**修复**：启动时对弱口令 fail-fast（生产模式）；compose 去掉危险默认值，改为缺失即报错（`${DB_PASSWORD:?required}`）。

### S-4 限流器 fail-open + 非原子实现（P2）

`security/rate_limit_dep.py:17-18`：限流器不可用时静默放行（login 端点 5 次/60s 保护失效）；`security/rate_limiter.py:80-84`：INCR 与 EXPIRE 非原子（进程崩溃则 key 永不过期，用户被永久限流）。

**修复**：改为 Lua 脚本原子实现；fail-open/fail-closed 按端点敏感度区分（login 应 fail-closed 或降级为本地窗口计数）。

### S-5 异常详情泄露（P2）

`websocket.py:370` 把内部异常 `str(e)` 直接发给客户端；`main.py:687-688` JWT 解码失败静默 pass，无法区分 token 过期与签名错误。

### S-6 Prompt 注入防护粗糙（P2）

`security/prompt_guard.py:47-54`：`python:`、`SELECT.*FROM`、`exec\(` 误伤面大；`sanitize_user_input` 命中后直接删除片段（:140），句子语义错乱后再喂 LLM；`check_injection`（拒绝）与 `sanitize`（删除）对同一输入行为不一致。

**修复**：统一策略为「检测 → 拒绝并提示」，删除 sanitize 路径；模式集收敛到真正的注入特征。

---

## 五、性能与稳定性问题

### P-1 角色锁 TTL 远小于实际 Tick 耗时（P1）

`tick.py:47-48`：`LOCK_TTL = 30` 秒，但一次 Tick 最多含 4 次 LLM 调用（ReAct 3 轮 + chat_with，每次 timeout=30s），实际耗时可远超 30s。锁中途过期后同一角色可被并发执行，锁形同虚设。且 `locks.py:89-95`、`engine.py:124-126`、`tick.py:100` 释放锁用无条件 `redis.delete`，不校验持有者——锁过期被他人获取后，finally 中的 delete 会误删他人锁。

**修复**：锁带唯一 token + Lua compare-and-delete 释放；增加看门狗续租（WorldEngine 已有续租，CharacterTick 应对齐）；或把锁 TTL 与 LLM 超时预算挂钩。

### P-2 fire-and-forget 任务泄漏（P1）

`messaging/service.py:434-444`：`asyncio.create_task(pm_service.update_memory(...))` 未保存引用（事件循环只持弱引用，任务可能被 GC 提前回收静默丢失），无取消逻辑、无异常回调，外层还包了规范禁止的 `except Exception: pass`（:445-446）。`main.py:230,272,300`：lifespan 在 yield 之前抛异常时已创建的后台任务无人取消。

**修复**：建立统一的 `BackgroundTaskRegistry`（创建即注册、异常回调记日志、shutdown 统一取消并等待）。

### P-3 熔断器 HALF_OPEN 不限制并发试探（P1）

`cost_control/circuit_breaker.py:159-160`：HALF_OPEN 状态下 `can_execute()` 对所有并发调用者返回 True，半开期间流量全部涌入下游；且状态机读写为非原子两步操作，多协程下存在竞态。

**修复**：HALF_OPEN 用 Redis 分布式信号量限制试探调用为 N=1；状态迁移用 Lua 原子化。

### P-4 OneBot 无消息去重，群聊有 LLM 调用风暴风险（P1）

`adapters/onebot.py:390-409`：不记录已处理 message_id，OneBot 重发即重复回复、重复写库。`service.py:128-261`：`onebot_group_at_only=False`（默认值）时每条非 @ 群消息触发一次 LLM 判定，活跃群造成调用风暴；且 `GROUP_REPLY_PROBABILITY_CAP=0.7` + 「LLM 说不回复仍有 15% 概率回复」+「LLM 出错 30% 概率回复」三层叠加，实际回复率远高于直觉，有刷屏风险。

**修复**：Redis SETNX 记录 message_id（TTL 10 分钟）；群消息判定加启发式前置过滤（只有命中角色名/疑问句/情绪词才进 LLM 判定）；三层概率逻辑合并为单一决策函数并给出可预期的最终概率上限。

### P-5 分享推送扇出放大 + Web 端推送 100% 静默失败（P1）

- `tick.py:1267-1338`：一次分享对该角色所有 QQ 会话（上限 100）逐一私聊推送，无聚合去重。
- `proactive_sharing.py:460-539`：每个会话一条 messages 行 + 一次 WS 推送 + 一条 Redis 通知，在 Tick 链路内同步 await，拉长角色 Tick。
- **类型 bug**：`proactive_sharing.py:495` 把 `UUID` 类型 `character_id` 传给 `WebSocketManager.send_to_user`，而连接表 key 是 URL 中的 `str`——key 永不相等，**Web 端实时分享推送 100% 静默失败**（send_to_user 返回 False 且无日志）。

**修复**：连接表 key 统一为 `str(character_id)`；扇出写入改为批量 insert + 后台任务投递；推送目标聚合（同用户多会话去重）。

### P-6 429 检测用字符串匹配（P1）

`main.py:468-476`：`"429" in error_str or "RateLimitError" in error_str`——错误消息中碰巧含 "429"（QQ 号、内容数字）即误判限流并中止整个批次。退避恢复条件 `success_count > 0` 也过于宽松（部分限流时不退避）。

**修复**：捕获具体的 `openai.RateLimitError` / HTTP 状态码类型；按失败比例驱动退避。

### P-7 热更新假生效（P1）

`config_runtime.py:83-86` 用 `setattr` 突变全局 settings，但：`character_max_concurrent` 更新后 `CharacterTickEngine.SEMAPHORE`（类属性，tick.py:71-72）不重建；`log_level` 更新后不重调 `setup_logging`；`llm_daily_budget_usd` 更新后 `BudgetManager`（lifespan 用旧值构造，budget_manager.py:95）不刷新。PUT /admin/config 返回 success 但行为没变。

**修复**：为每个可热更新配置项定义「应用器」注册表（更新 → 调用对应组件的 apply 方法），不支持热更新的项从可写集合移除。

### P-8 World Engine 若干缺陷（P2）

- 所有 Evolution 的 `setup()` 方法从未被调用（死初始化路径，靠 evolve 内 fallback 碰巧工作）。
- `engine.py:243-249`：快照判断 `tick_id % interval == 0`，热更新 interval 后取模基线漂移。
- `engine.py:348-442`：`_save_world_events` 吞掉所有异常仅记日志，PG 写失败时差分事件永久丢失且无指标告警。
- `engine.py:69-70`：`_last_persisted_state` 仅存内存，重启后第一批事件全量重复写入。
- `scene_evolution.py:20,80`：`world:scene:visitors` 键全库无人写入，场景拥挤度永远为 0（死特性）。
- `engine.py:286-299`：加载 key（`scenes`）与演化器返回 key（`locations`）不一致，事件快照有一拍延迟。
- shutdown 链路无 per-step 容错：`await world_engine.stop()` 内 Redis 断连抛异常会导致后续 `redis.close()` 不执行。

---

## 六、配置体系问题

### C-1 scenes.yaml 与 world-map.yaml 职责割裂（P1）

场景命名空间分裂在两个文件且无交叉校验（P0-9 的不一致即是后果）。AGENTS.md §3.4 配置真相源表完全没提 `scenes.yaml`（规范黑户）；`docs/town-design.md` 反复声称场景定义在 `world-map.yaml`（与现实矛盾）。

**修复**：合并为单一 `configs/world.yaml`（场景元数据 + 连通矩阵同文件，加载时校验），或至少在 loader 中加跨文件一致性校验；更新 AGENTS.md 真相源表。

### C-2 events.yaml 的 activities 词表无 schema 约束（P2）

`configs/events.yaml:7,15,22` 的 activities 与 `scenes.yaml` 词表靠约定维持。建议加载时校验活动词表并集，非法 activity 启动报错。

### C-3 三套配置读取方式并存（P2）

Settings（pydantic-settings）、RuntimeConfig（Redis 热更新）、`adapters/lark.py:64,120-121` 直接 `os.environ.get`（绕过 Settings 无校验）。且 `api/admin.py:875` 每次请求 `GET /admin/config` 都重新 `Settings()` 解析 .env。

### C-4 配置损坏静默降级（P2）

`config_runtime.py:122-132,144-147`：Redis 读取失败、校验失败均只记 warning 后静默用默认值继续——配置损坏时系统以管理员不知情的状态运行。应区分「首次启动无配置」（正常）与「已有配置损坏」（告警 + 拒绝热更新写入）。

---

## 七、前端问题

### F-1 实时链路全面脱节（P1）

- `src/hooks/useWebSocket.ts`（含完整指数退避重连）**零调用方**，纯死代码。
- 实际实时方案全部是轮询（health 5s、world 5s、logs 5s、notifications 10s 等，`lib/queries.ts` 9+ 处 `refetchInterval`）。
- `docs/api-spec.md:575-578` 文档化的 4 个 WS 端点（`/ws/dashboard`、`/ws/characters/{id}` 等）后端均未实现；`docs/frontend-design.md:373-411` 设计的 websocket-store 不存在。

**修复**：二选一——（a）实现 `/ws/dashboard` 推送并接入 useWebSocket hook + Zustand store；（b）若短期不做推送，把文档降级为「轮询方案」并删除死代码 hook。推荐（a），世界状态与通知是推送的天然场景。

### F-2 业务规则混入前端（P1/P2）

- `routes/characters.$characterId.tsx:81`：聊天发送硬编码 `userId: "web_user"`，架空鉴权体系。
- `routes/notifications.tsx:166-174`：页面内置 mockTemplates + `Math.random()` 生成模拟通知写入后端。
- `routes/metrics.tsx:112,128`：绕过统一 API 客户端直接 `fetch("/metrics/")` + 手写 setInterval，无鉴权头、无 401 处理。

### F-3 API 类型契约无同步机制（P2）

647 行手写类型与后端零同步；`docs/development-guide.md:46` 提到的 `pnpm gen:api` 脚本在 package.json 中不存在；zod 是声明依赖但 src 零导入（API 响应无运行时校验）。`queryKeys` 工厂定义后 30+ 个 hook 全部用内联裸 key，工厂名存实亡。

**修复**：落地 openapi-typescript 生成（FastAPI 自动产出 OpenAPI，成本极低）；对关键响应加 zod 解析；统一使用 queryKeys 工厂。

### F-4 其他（P2）

- 前端零测试（vitest/playwright 依赖与脚本存在但无任何测试文件）。
- 401 跳转用 `window.location.href` 整页刷新而非 router 导航。
- 乐观更新手工拼临时 id，与服务端排序耦合，易出现重复/乱序。
- `tsconfig.tsbuildinfo` 被 git 追踪（应入 .gitignore）。
- README 宣称「TypeScript 7.0」版本号存疑。

---

## 八、部署与工程化问题

### D-1 三份 compose 文件互相矛盾（P1）

| 维度 | compose.yml | infra.yml | win.infra.yml |
|---|---|---|---|
| PG 镜像 | 自定义构建基于 **pg17** | 同左 | `pgvector/pgvector:pg18` |
| PG 端口 | 5432 | 5432 | **5433** |
| Redis | `redis:8.0-alpine` | 同左 | `redis:alpine`（非 8.0） |
| 数据卷 | 命名卷 | 命名卷 | bind mount `./data/*` |

README 宣称 PostgreSQL 18，主 compose 却是 pg17；win 版 5433 与 `.env.example` 的 5432 不一致，开发者照文档配置连不上。

**修复**：三份合并为「一份 base + override」结构；统一 PG 18；win 差异（bind mount、端口）用 override 表达并在文档中说明。

### D-2 健康检查与资源限制不全（P2）

`docker-compose.yml:40-49`：redis 无 healthcheck，backend 仅等 `service_started`；全栈无 `deploy.resources` 限制；可观测性组件镜像 tag 漂移（`prometheus:latest`、jaeger `1.60` vs `latest`）。

### D-3 文档引用的组件不存在（P2）

`docs/deployment.md:171-173` 引用不存在的 `otel-collector` 服务与 `./otel-collector.yaml`；`docs/docker-deployment.md:360` 提到 pgbouncer 服务，compose 中根本没有（README 技术栈里的 PgBouncer 同样未落地）。

### D-4 仓库卫生（P2）

- `data/minio/` 是历史遗留（当前 compose 已无 minio 服务），bind mount 下会随容器运行持续膨大，需文档说明或清理。
- `tmp/` 下 26 章 tutorial 与 docs/ 主题高度重叠，要么转正进 docs/ 要么删除，长期放 tmp/ 会腐烂成第二套过时文档。
- 根目录 `__pycache__/` 是在仓库根误跑 pytest 留下的（测试应在 `packages/backend/` 下运行）。
- `packages/frontend/tsconfig.tsbuildinfo` 被 git 追踪。

---

## 九、文档一致性问题

### DC-1 幽灵配置 config.yaml（P1）

不存在于仓库，但被三处正式文档化：`README.md:213`（项目结构）、`docs/config-reference.md:138-246`（整节示例）、`docs/action-system.md:316`（「从 config.yaml 加载自定义 Action」）。后端代码零引用——实际真相源只有 `.env` + `src/config.py`。

**修复**：删除三处引用，或实现 config.yaml 加载（不推荐，会引入第三配置真相源）。

### DC-2 目录结构文档失真（P1）

`README.md:189` 写的 `src/agents/` 不存在；`docs/development-guide.md:60-85` 的结构图大面积失真（`core/world_engine.py`、`core/actions/`、`agents/`、`messaging/adapters/` 均与实际不符）；`docs/architecture.md:272`、`docs/messaging-service.md:623` 等仍引用旧路径 `character_tick.py`。

### DC-3 历史分析文档未归档（P2）

`docs/gap-analysis.md`（2026-07 审查，本身质量高，指出 4 项未完成：CONTRIBUTING.md、多模型备用源、核心模块测试、速率限制覆盖）与 `docs/mcp-improvement-analysis.md`（MCP 架构已删除，纯历史记录）应与现行文档分离——建议移入 `docs/archive/` 并在标题标注归档日期，避免误导。

---

## 十、测试覆盖缺口

现状：`tests/` 15 个文件 2750 行，其中**可观测性占 929 行（1/3），性价比极低**。全部不连真实 DB/Redis（纯单元测试），集成路径零覆盖。

**按风险排序的缺口**：

| 优先级 | 缺失测试 | 风险 |
|---|---|---|
| P0 | `CharacterTickEngine`（1356 行，全系统最核心最脆弱的文件）零测试 | 感知/决策/执行/双写/库存序列化（P0-2）无回归保护 |
| P0 | 状态一致性：PG/Redis 双写顺序、工具 delta、序列化往返 | P0-1/P0-2/P0-3 类问题会持续复发 |
| P1 | `WorldEngine`（leader election、tick 循环、事件去重、快照） | 世界推进逻辑无保护 |
| P1 | `MessageService` / `WebSocketManager` / OneBot 适配器 | 连接替换竞态、消息去重、群聊概率逻辑无覆盖 |
| P1 | API 层集成测试（13 个路由文件零测试） | PUBLIC_GET 白名单、RBAC 默认 admin 无兜底 |
| P1 | Repository 集成测试（分区表、向量检索、SKIP LOCKED） | 数据层行为未验证 |
| P2 | config_runtime 热更新、locks.py、tools（shop/social 含大量业务规则） | 假热更新（P-7）无检测 |

**建议**：引入 testcontainers（PG + Redis）做集成测试基座；优先为 P0 问题修复各配一个回归测试（修复即锁定）。

---

## 十一、死代码清单

以下代码定义了但从未被调用，建议删除或接入（接入需单独评估）：

| 位置 | 内容 | 建议 |
|---|---|---|
| `tick.py:1352-1353` | `tick_all_active`（含 asyncio.gather） | **接入**（修复 P0-4 时使用） |
| `cost_control/decorators.py:59-143` | `with_cost_control` 装饰器 | 接入或删除（修复 P0-5 时决定） |
| `actions/move.py:26-31,34-63` | `_move_executor`、`compute_move_duration` | **接入**（修复 P0-7 时使用） |
| `modules/movement/system.py` | MovementSystem（路径规划、开放时间校验） | 接入 Tick 主链路 |
| `core/world/evolutions/*.py` | 所有 `setup()` 方法 | 接入初始化流程或删除 |
| `scene_evolution.py:20,80` | `world:scene:visitors` 拥挤度特性 | 接入或删除特性 |
| `adapters/lark.py` | 整个 Lark 适配器（未挂载路由，挂载后 webhook 必被鉴权拦截） | 删除或完成接入 |
| `db/models/character.py:74` | `CharacterState.version` 乐观锁字段（全库无人递增/校验） | 实现乐观锁或删除字段 |
| `api/actions.py:68-69` | `getattr(action, "preconditions"/"effects")` 返回假数据 | 修正返回真实字段 |
| `frontend/src/hooks/useWebSocket.ts` | WebSocket hook | 接入（修复 F-1）或删除 |
| `proactive_sharing.py:44-57` | 可分享动作白名单含 7 个 ActionRegistry 中不存在的动作 | 修正词表 |
| `tick.py:46,71-72` | SEMAPHORE 类属性（主循环串行导致从未生效） | 随 P0-4 修复激活 |

---

## 十二、修复路线图

### 第一阶段：数据与安全止损（1-2 周，全部 P0）

1. **P0-2 状态序列化统一** + 库存回归测试（1 天，风险最高收益最大）
2. **P0-1 工具 delta 纳入 PG 事务** + 双写顺序统一（2 天）
3. **P0-3 启动回灌机制**（1 天）
4. **P0-8 鉴权堵漏**：PUBLIC_GET 白名单收敛、WS 鉴权、OneBot token 校验（2 天）
5. **S-1 RBAC 默认角色改 viewer** + admin 端点依赖审计（0.5 天）
6. **P0-9 补全移动矩阵** + loader 校验（0.5 天）
7. **P0-10 修复 postgres 构建**（0.5 天）
8. **P0-5 成本控制统一挂载到 llm/client**（2 天）

### 第二阶段：核心架构修复（2-3 周）

9. **P0-4 角色 Tick 并发化**（复用 tick_all_active + 修复 Semaphore 热更新）
10. **P0-7 executor 抽象落地** + move 目标校验 + MovementSystem 接入
11. **P0-6 Prompt 全部外置** + YAML 缺失 fail-fast
12. **P0-11 双配置体系统一**
13. **P-1 锁机制修复**（token + compare-and-delete + 续租）
14. **A-3 AppContext 依赖容器收敛**（消除双轨全局与 30+ 处 None 防御）
15. **A-7 真实 token usage 贯通**（预算/持久化/指标单轨）
16. **P-4/P-5 消息服务加固**（去重、扇出优化、UUID/str 类型 bug）

### 第三阶段：工程化与一致性（2 周）

17. **测试基座**：testcontainers 集成测试 + 核心引擎测试补齐
18. **F-1 前端实时链路**（实现 /ws/dashboard 或降级文档）
19. **F-3 openapi-typescript 类型生成落地**
20. **D-1 compose 合并为 base + override**
21. **文档大扫除**：config.yaml 幽灵引用、目录结构图、历史文档归档
22. **死代码清理**（第十一节清单逐项处置）

### 第四阶段：增强（按业务优先级）

23. 多模型备用源（gap-analysis.md 已列为 P1 未完成项）
24. 对账任务（Redis vs PG 定期 diff）
25. 告警规则（可观测性指标已埋点但无告警）
26. CONTRIBUTING.md / ADR / CHANGELOG

---

## 附：问题统计

| 级别 | 数量 | 领域分布 |
|---|---|---|
| P0 | 11 | 状态一致性 3、LLM/成本 2、安全 1、Action 1、配置 2、并发 1、部署 1 |
| P1 | ~35 | 分层穿透、双写顺序、锁、熔断器、消息服务、前端实时、假热更新、文档幽灵 |
| P2 | ~45 | 死代码 12 项、structlog %s 误用 4 处、防御性兜底 10+ 处、仓库卫生、tag 漂移等 |

> 本报告所有问题均附文件:行号证据，可直接按图索骥。修复时建议每个 P0/P1 问题配一个回归测试，防止复发。
