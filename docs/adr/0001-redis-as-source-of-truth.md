# 0001 - Redis 作为实时状态真相源，PG 作为镜像

- 状态：Accepted
- 日期：2026-08-23
- 关联：docs/design-improvement-and-fixes.md P0-1/P0-2/P0-3、A-2、roadmap #24

## 背景（Context）

角色实时状态（位置/体力/金钱/库存等）被两条路径高频读写：

1. **Tick 链路**：每 30 秒全量角色推进，读改写状态，含多次 LLM 调用。
2. **API/推送链路**：前端地图、角色卡、聊天上下文随时读取最新状态。

PostgreSQL 提供事务与持久性，但单表高频点写 + 每次决策前多列读取，
延迟与连接压力都不可接受；纯内存方案又无法在进程重启后恢复世界。

## 决策（Decision）

**Redis 是实时状态的唯一真相源（source of truth）；PostgreSQL 保存镜像与历史。**

- 写顺序固定为「先 PG 事务提交，再写 Redis」；工具产生的 delta 与 ActionRecord
  在同一 PG 事务内落库。
- Redis 键 `char:{id}:state` 的编解码统一走 `src/core/state_codec.py`
  （标量 str、复合类型 JSON），禁止调用方自行序列化。
- 一致性保障分两层：
  - **启动回灌**（rehydration.py）：Redis 键缺失时从 PG 镜像恢复；
  - **运行期对账**（reconcile.py）：每 10 分钟 diff 两库，字段漂移以 Redis 为准
    修正 PG，并打 Prometheus 指标供告警。

## 备选方案（Alternatives）

| 方案 | 否决原因 |
|------|----------|
| PG 为唯一真相源 | Tick 每 30s × N 角色 × 多列读写，asyncpg 连接池成为瓶颈；LLM 决策前的感知查询延迟直接放大 Tick 轮长 |
| 双库同时为真相源（按字段划分） | 字段级真相源分裂使「谁对」无法判定，对账失去基准 |
| 仅 Redis 无镜像 | Redis 故障即世界归零，陪伴类产品不可接受 |

## 后果（Consequences）

**正面**

- Tick 感知/写入全部走 O(1) Redis 哈希操作，轮长由 LLM 延迟主导而非存储。
- PG 镜像天然构成审计/回放数据集；重启后世界可恢复。

**负面 / 义务**

- 所有状态写入必须遵守双写顺序，新增写入点需 review 是否纳入同一事务。
- 对账是最终一致（最长 10 分钟窗口）而非强一致——接受，因为漂移只影响
  PG 侧统计展示，不影响运行时行为。
- Redis 必须开启 AOF 持久化（compose 已配置）以缩小故障窗口。
