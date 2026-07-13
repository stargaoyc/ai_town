# 开发路线图

> 本文档定义 AI Town 的开发阶段、里程碑、任务分解与优先级排序。采用**渐进交付**策略，每个阶段交付可运行、可验证的核心功能。

---

## 一、开发阶段概览

```text
Phase 0: 项目初始化           ✅ 已完成
   ├─ 创建项目结构（monorepo）
   ├─ 初始化 packages（backend / frontend / mcp-servers）
   ├─ 配置依赖管理与构建脚本
   └─ 数据库 DDL 迁移脚本
        ↓
Phase 1: 世界引擎核心          ✅ 已完成
   ├─ World Tick 演化列表（Time/Weather/Scene）
   ├─ Character Tick 五阶段闭环
   ├─ Action 系统注册与执行（事务化）
   └─ 记忆系统（MemoryEpisode + pgvector）
        ↓
Phase 2: 角色与小镇            ✅ 已完成
   ├─ 角色卡导入与状态初始化
   ├─ 小镇场景系统与移动矩阵
   ├─ 作息系统与动态耗时
   └─ 角色关系图谱
        ↓
Phase 2.5: 性能与数据完整性优化 ✅ 已完成
   ├─ memory_episodes HASH 分区（16）+ 父表 HNSW
   ├─ 异步 embedding worker（解耦 Tick 与 LLM）
   ├─ world_events 差分事件表（替代高频快照）
   ├─ reflection_sources 外键中间表
   ├─ character_states 乐观锁
   └─ 覆盖索引优化
        ↓
Phase 3: 外部交互              ✅ 已完成
   ├─ 消息服务（QQ/飞书/Web WebSocket）
   ├─ 主动分享链路
   ├─ MCP Server 自研（code-executor / shop-simulator / character-social / knowledge-base）
   └─ MCP Server 社区集成（web-search / weather）
        ↓
Phase 3.5: 安全与可靠性        ✅ 已完成
   ├─ API 鉴权（JWT + API Key）
   ├─ LLM 成本控制（日预算上限 + 熔断）
   ├─ Prompt 注入防护
   └─ Redis ↔ PG 一致性保证
        ↓
Phase 4: 可观测性与运维        ✅ 已完成
   ├─ OTel Trace 全链路埋点
   ├─ 结构化 JSON 日志 + trace_id 关联
   ├─ Prometheus Metrics
   └─ Langfuse LLM 追踪
        ↓
Phase 5: 前端 Dashboard        ✅ 已完成
   ├─ 角色状态可视化
   ├─ 世界地图与场景热力图
   ├─ Trace/Memory 调试面板
   ├─ 管理命令入口
   ├─ ErrorBoundary + Skeleton 骨架屏
   └─ WebSocket 重连（指数退避）
```

---

## 二、Phase 0：项目初始化 ✅ 已完成

### 任务清单

| 任务 | 状态 | 验收 |
|------|------|------|
| 创建 monorepo 结构 | ✅ | pnpm workspace 正常 |
| 初始化 backend（Python 3.13 + uv） | ✅ | `uv sync` + `uvicorn` 启动 |
| 初始化 frontend（React 19 + Vite 8） | ✅ | `pnpm dev` 启动 |
| 数据库 DDL（pgvector + pg_uuidv7 + HNSW） | ✅ | `alembic upgrade head` 成功 |
| Docker Compose 基础设施 | ✅ | postgres/redis 可启动 |
| 环境变量 `.env.example` | ✅ | 含所有必填变量 |

---

## 三、Phase 1：世界引擎核心 ✅ 已完成

### 任务清单

| 任务 | 状态 | 验收 |
|------|------|------|
| `TimeEvolution` 虚拟时钟 | ✅ | Tick 推进 10 虚拟分钟 |
| `WeatherEvolution` 天气演化 | ✅ | 60 Tick 更新一次 |
| `SceneEvolution` 场景演化 | ✅ | 拥挤度更新 |
| `EventEvolution` 事件触发 | ✅ | 樱花祭触发 |
| Character Tick 五阶段闭环 | ✅ | 感知→过滤→决策→执行→记忆 |
| ActionRegistry + 10+ Action | ✅ | move/life/work/social |
| 记忆系统 + pgvector | ✅ | HNSW 检索可用 |
| LangChain 1.3 + LangGraph 1.2 集成 | ✅ | LLM 决策可用 |

---

## 四、Phase 2：角色与小镇 ✅ 已完成

### 任务清单

| 任务 | 状态 | 验收 |
|------|------|------|
| 角色卡 Pydantic v2 校验 + YAML 导入 | ✅ | 4 个角色卡导入成功 |
| 小镇场景加载器 + 世界地图 | ✅ | 12 场景 + 移动矩阵 |
| 作息系统（early_bird/normal/night_owl） | ✅ | 活动水平判定正确 |
| 动态耗时系统（天气/拥挤度/体力/情绪） | ✅ | 四因子修正 |
| 移动系统（路径计算 + Dijkstra） | ✅ | 路径规划可用 |
| 角色关系图谱（双向同步 + 自动升级） | ✅ | stranger→best_friend |
| 11 个 API 端点集成 | ✅ | 全部测试通过 |

---

## 五、Phase 2.5：性能与数据完整性优化 ✅ 已完成

> 基于八轮数据库审查意见的系统性优化。已创建 [0002_optimize.py](../packages/backend/alembic/versions/0002_optimize.py) v6 迁移脚本 + [0001_init.py](../packages/backend/alembic/versions/0001_init.py) v8 修复。

### 第一轮审查（v1 → v2）

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 1 | 全局 HNSW + character_id 过滤 → 召回率崩塌 | 致命 | HASH 分区（16 分区）+ 父表 HNSW | ✅ |
| 2 | Action:Memory 1:1 写放大 → Tick 阻塞 | 高 | materialized 标志 + 异步 embedding worker | ✅ |
| 3 | DEFAULT 分区兜底 → 慢查询定时炸弹 | 中 | 删除 DEFAULT 分区 | ✅ |
| 4 | world_snapshots 全量 JSONB → IO 灾难 | 高 | world_events 差分事件表 | ✅ |
| 5 | personality TEXT[] 与 traits JSONB 冗余 | 中 | 统一到 traits.personality | ✅ |
| 6 | reflections.source_memory_ids 无外键 | 中 | reflection_sources 中间表 | ✅ |
| 7 | messages 分区键缺陷 | 中 | conversation_id 覆盖索引 | ✅ |
| 8 | character_states 缺少乐观锁 | 中 | version 字段 | ✅ |
| 9 | pg_uuidv7 扩展可用性风险 | 低 | 应用层 uuid6 库兜底（已有） | ✅ |
| 10 | messages.user_id 索引覆盖 | 低 | INCLUDE 覆盖索引 | ✅ |
| 11 | HNSW ef_construction=64 偏低 | 中 | 提升至 128 | ✅ |

### 第二轮审查（v2 → v2.1）

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 12 | personality 迁移 NULL || jsonb = NULL | 致命 | COALESCE 防御 | ✅ |
| 13 | 手动子分区索引 → 运维噩梦 | 高 | HNSW 索引在父表创建（自动传播） | ✅ |
| 14 | DEFAULT 分区静默写入 | 中 | 删除 DEFAULT 分区 | ✅ |
| 15 | character_states 并发覆盖 | 高 | 乐观锁 version 字段 | ✅ |
| 16 | 覆盖索引 INCLUDE(content) 膨胀 | 中 | 移除 content，仅轻量字段 | ✅ |

### 第三轮审查（v2.1 → v3）

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 17 | check_partition_exists 触发器是死代码（PG 分区路由在 BEFORE INSERT 之前执行） | 致命 | 删除触发器，PG 原生报错足够清晰 | ✅ |
| 18 | reflection_sources.memory_id 无外键（悬空引用） | 高 | 增加 memory_character_id + 复合外键 ON DELETE CASCADE | ✅ |
| 19 | 事件溯源缺快照闭环（冷启动随时间线性变慢） | 高 | 恢复 world_snapshots 表（每 1000 Tick 快照） | ✅ |
| 20 | character_states fillfactor=100 → HOT 更新失败 → 膨胀 | 中 | fillfactor=85 + autovacuum 调优 | ✅ |
| 21 | updated_at 触发器仅 character_states 有 | 中 | 通用 update_updated_at() 覆盖 characters/character_states/plans | ✅ |
| 22 | characters/plans 缺 updated_at 字段 | 中 | 补充 updated_at 字段 | ✅ |
| 23 | BRIN 索引与月分区重复优化 | 低 | 不使用 BRIN，分区裁剪 + B-tree 足够 | ✅ |
| 24 | 缺少 COMMENT 元数据注释 | 中 | COMMENT ON TABLE/COLUMN 全覆盖 | ✅ |
| 25 | 分区预创建只有约束无自动化 | 中 | pre_create_partitions() PL/pgSQL 函数 | ✅ |
| 26 | downgrade 脚本不可用（数据永久丢失） | 中 | 简化为 raise exception（只升级不降级） | ✅ |
| 27 | world_events 事件风暴（无变化也写入） | 中 | 事件去重（仅状态变化时写入） | ✅ |
| 28 | memory_episodes.character_id 无外键 → 孤儿数据 | 中 | v3: 应用层校验 → v4: DB 外键 REFERENCES characters(id) ON DELETE CASCADE | ✅ |

### 第四轮审查（v3 → v4）

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 29 | 「分区表不能加外键」是认知错误（PG 11+ 支持） | 致命 | memory_episodes.character_id 补充外键 REFERENCES characters(id) ON DELETE CASCADE | ✅ |
| 30 | world_events 无幂等约束（重试/重启导致重复事件） | 高 | UNIQUE(tick_id, event_type) + ON CONFLICT DO NOTHING | ✅ |
| 31 | VACUUM FULL 在迁移中阻塞全表读写 | 高 | 移除自动执行，改为注释说明手动维护 | ✅ |
| 32 | 删除 DEFAULT 分区可能静默丢数据 | 高 | 删除前 DO 块检查数据量，有数据则 RAISE EXCEPTION | ✅ |
| 33 | pre_create_partitions() 异常捕获过宽（WHEN OTHERS） | 中 | 收紧为 undefined_table + duplicate_table | ✅ |
| 34 | HASH 分区「便于扩展」措辞误导 | 低 | 文档明确说明 HASH 分区数固定，扩容需全表重分布 | ✅ |
| 35 | 异步向量化并发控制 | — | 已由 FOR UPDATE SKIP LOCKED 解决，无需修改 | ✅ |

### 第五轮审查（v4 → v5）

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 36 | 设计文档（data-model.md）与迁移脚本/ORM 严重脱节（字段名不一致：energy/hunger vs stamina/satiety、horizon vs type、status vs is_active 等） | P0 | 选择**方案 B**：改文档对齐代码（避免破坏既有业务逻辑），全文 DDL 逐表校对修正 | ✅ |
| 37 | reflections.related_episodes 废弃字段未清理（已被 reflection_sources 替代，双写风险） | P0 | 迁移增加 `ALTER TABLE reflections DROP COLUMN IF EXISTS related_episodes`，ORM 同步移除 | ✅ |
| 38 | reflections 表仍残留 summary/detail/source_memory_ids/importance/embedding 字段引用 | P0 | 文档对齐为仅 `content` 单字段，检索 SQL 改用 content 全文匹配 | ✅ |
| 39 | relations 表文档使用 from_id/to_id，实际代码为 character_id/target_id | P0 | 文档 DDL 对齐代码，移除不存在的 tags/metadata/updated_at 字段 | ✅ |
| 40 | messages 段落仍引用 BRIN 索引（与"不使用 BRIN"原则矛盾） | 中 | 删除 `idx_msg_created_brin`，补充说明 | ✅ |
| 41 | world_events 未按月 RANGE 分区（高频写入表） | P1 | 延迟至 Phase 4，当前数据量未达分区阈值 | ⏳ Phase 4 |
| 42 | conversations/messages 表在迁移脚本中未创建（0001/0002 均缺失） | P1 | 延迟至 Phase 3 消息服务阶段补建迁移 | ⏳ Phase 3 |
| 43 | action_records.related_characters 为 JSONB，memory_episodes.related_characters 为 UUID[]（类型不统一） | P1 | 延迟至 Phase 4 统一，需评估查询模式后再定 | ⏳ Phase 4 |
| 44 | TEXT + CHECK 与 ENUM 类型选型未统一 | P2 | 延迟至 Phase 4，ENUM 修改需 ALTER TYPE 影响较大 | ⏳ Phase 4 |
| 45 | Schema 划分与权限体系未实施 | P2 | 延迟至 Phase 4 多租户/权限阶段 | ⏳ Phase 4 |
| 46 | HNSW 索引运维（重建/监控）缺方案 | P2 | 延迟至 Phase 4 可观测性阶段 | ⏳ Phase 4 |
| 47 | 软删除 vs 物理级联语义未统一 | P2 | 延迟至 Phase 4，需制定全表一致的删除策略 | ⏳ Phase 4 |

> **v5 核心结论**：经核查，ORM 模型与迁移脚本**实际一致**（无运行时风险），不一致主要发生在设计文档与代码之间。采用方案 B（文档对齐代码）而非方案 A（改迁移对齐文档），避免破坏已通过测试的业务逻辑。

### 第六轮审查（v5 → v6）

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 48 | ⚠️ P0: 0002_optimize 创建 messages 表覆盖索引，但 0001_init 未建 messages 表 → 迁移中断 | P0 | 移除 messages 索引创建，表+索引+分区统一推迟到 Phase 3 | ✅ |
| 49 | memory_episodes 重建大表 INSERT...SELECT 可能卡死（无超时保护） | P1 | 添加 statement_timeout=10min + lock_timeout=60s 显式超时 | ✅ |
| 50 | pre_create_partitions() action_records 无 undefined_table 异常捕获（与 messages 不一致） | P1 | 增加 undefined_table + duplicate_table 异常捕获 | ✅ |
| 51 | world_events 幂等约束 UNIQUE(tick_id, event_type) 粒度过粗？ | — | **非问题**：当前实现每 Tick 每类型仅写 1 条全量事件，约束正确。已在模型 docstring 文档化前提假设 | ✅ |
| 52 | personality 全表 UPDATE 持有长锁 | — | **过度优化**：仅 50 行角色数据，瞬时完成，无需分批 | ✅ |
| 53 | 迁移粒度过大（单迁移含 20+ 变更），故障定位困难 | P2 | 标记技术债，未来按职责拆分（当前不拆分，拆分风险高于收益） | ⏳ 技术债 |
| 54 | 角色删除级联扫描 16 分区开销 | P2 | 16 分区下可接受；64+ 分区需改应用层并行删除 | ⏳ Phase 4+ |
| 55 | reflection_sources 复合外键在分区表删除时扫描所有子分区 | P2 | 16 分区下可接受；64+ 分区需评估改为软删除+后台清理 | ⏳ Phase 4+ |

> **v6 核心结论**：P0 阻塞性 Bug（messages 表索引）已修复，迁移可正常执行。world_events 幂等约束经核查**非真实问题**（当前实现聚合写入，非逐实体写入），但已文档化前提假设以防未来误改。personality 分批更新在 50 行规模下属过度优化。

### 第七轮审查（v6 → v7）— 0001_init 致命缺陷修复

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 56 | ⚠️ P0: 0001_init action_records 用 op.create_table() 建普通堆表，无 PARTITION BY 声明 → 建子分区必报 "relation is not partitioned" | P0 | 改用原生 SQL `CREATE TABLE ... PARTITION BY RANGE (timestamp)`，复合主键 `(id, timestamp)` | ✅ |
| 57 | ⚠️ P0: 0001_init memory_episodes embedding 定义为 sa.Text，但 HNSW 索引用 vector_cosine_ops → 类型不匹配必失败 | P0 | 改用原生 SQL `embedding vector(1536)` 类型 | ✅ |
| 58 | pre_create_partitions() 用 CURRENT_DATE（无时区）计算分区边界，与 TIMESTAMPTZ 分区键不一致 | P1 | 改用 CURRENT_TIMESTAMP，边界变量改为 TIMESTAMPTZ 类型 | ✅ |
| 59 | 文档 characters.traits/character_states.inventory/plans.status 标注 NOT NULL DEFAULT，迁移实际为 nullable | 文档 | 按方案 B 对齐文档为 nullable，标注 ORM 层 default | ✅ |
| 60 | updated_at 触发器未覆盖 relations/world_snapshots | — | **不成立**：这两张表无 updated_at 字段 | ✅ |
| 61 | 异步向量化无失败处理（fail_count/last_error） | P2 | 延后至 Phase 3 EmbeddingWorker 增强阶段 | ⏳ Phase 3 |
| 62 | reflections 移除 embedding 导致语义检索退化 | 架构 | 延后至 Phase 4 补回 reflections.embedding + HNSW | ⏳ Phase 4 |
| 63 | 软删除(is_active)与物理级联(ON DELETE CASCADE)语义冲突 | 架构 | 延后至 Phase 4 统一删除策略 | ⏳ Phase 4 |
| 64 | relations 有向图无双向一致性触发器 | 架构 | 延后至 Phase 3 社交模块阶段 | ⏳ Phase 3 |
| 65 | 事件溯源缺少状态哈希校验闭环 | 架构 | 延后至 Phase 4 可观测性阶段 | ⏳ Phase 4 |
| 66 | stamina/satiety/money 等字段文档标注 NOT NULL DEFAULT，迁移用 Python default（nullable） | 文档 | 已知差异，与 #59 同类，后续统一处理 | ⏳ 技术债 |

> **v7 核心结论**：0001_init 从第一版起就存在两个致命缺陷（action_records 未分区 + embedding 类型错误），导致整个迁移链从未能成功执行。v7 修复后 0001_init → 0002_optimize 迁移链可正常 `alembic upgrade head`。

### 第八轮审查（v7 → v8）— 数据迁移类型转换 + 索引补全

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 67 | ⚠️ P0: 0002_optimize 数据迁移 SELECT related_characters 直接从 JSONB 插入 UUID[] → 隐式转换失败 | P0 | 添加 `ARRAY(SELECT jsonb_array_elements_text(...))::uuid[]` 显式转换 | ✅ |
| 68 | pre_create_partitions 仅启动时执行，长期运行 >3 月漏建分区 | P1 | Phase 3 增加 APScheduler 每月定时调度 | ⏳ Phase 3 |
| 69 | 异步向量化无失败处理（fail_count/last_error），故障记忆反复重试 | P1 | Phase 3 新增 fail_count/last_error 字段 + 最大重试次数 | ⏳ Phase 3 |
| 70 | action_records 缺失 idx_ar_action/idx_ar_params 索引（文档声明但迁移未建） | P2 | 0001_init 补充索引 + ORM 模型同步 | ✅ |
| 71 | module_configs 等表 updated_at 触发器未覆盖 | — | **不成立**：module_configs 表不存在于任何迁移 | ✅ |
| 72 | world_events 幂等约束粒度过粗 | — | **非问题（第三次驳回）**：每 Tick 每类型仅写 1 条全量事件 | ✅ |
| 73 | personality 全表更新长锁 | — | **过度优化**：50 行数据瞬时完成 | ✅ |

> **v8 核心结论**：最后一个 P0（JSONB→UUID[] 类型转换）已修复，迁移链 0001→0002 可完整执行。P1 增强项（分区定时调度、向量化失败处理）纳入 Phase 3。

### 新增/修改文件

| 文件 | 说明 |
|------|------|
| `alembic/versions/0002_optimize.py` | 数据库优化迁移脚本 v6（v5 增加 related_episodes 清理，v6 修复 messages P0 + 超时保护 + 异常捕获） |
| `src/db/models/world_event.py` | 世界变更事件模型 |
| `src/db/models/world_snapshot.py` | 世界快照模型（v3 恢复） |
| `src/db/models/reflection_source.py` | 反思来源中间表模型（v3 增加复合外键） |
| `src/db/models/character.py` | 角色模型（v3 增加 updated_at） |
| `src/db/models/plan.py` | 计划模型（v3 增加 updated_at） |
| `src/db/repositories/snapshot_repo.py` | 世界事件+快照 Repository（v3 增加 WorldSnapshotRepository） |
| `src/db/repositories/memory_repo.py` | 记忆 Repository（v3 增加 character_id 校验） |
| `src/core/world_engine.py` | 世界引擎（v3 增加快照保存+事件去重） |
| `src/memory/embedding_worker.py` | 异步 embedding 生成 worker |

### 容量估算修正

| 项目 | 原估算 | 修正后 | 说明 |
|------|--------|--------|------|
| memory_episodes 存储 | 80 GB/年 | 108 GB/年（向量）+ 50 GB（HNSW 索引） | 1536 维 float32 = 6KB/条 |
| world_events（差分事件） | 1.2 TB/年（30s 全量落盘） | ~50 MB/年（差分事件，仅状态变化时写入） | 事件去重后写入量极低 |
| world_snapshots（定期快照） | — | ~500 MB/年（每 1000 Tick 一次） | 冷启动恢复用，启动时间恒定 |
| 建议冷热分离 | — | 3 个月前 action_records.params 迁移至对象存储 | PG 仅存轻量索引字段 |

### 第九轮审查（v8 → v9）— Phase 3 启动：消息服务 + MCP Server + P1 延后项处理

| # | 问题 | 严重度 | 解决方案 | 状态 |
|---|------|--------|----------|------|
| 74 | P0: memory_episodes JSONB→UUID[] 类型转换失败（再次提出） | — | v8 已修复，本轮确认 | ✅ |
| 75 | P1: world_events 幂等约束粒度过粗（第四次提出） | — | **非问题（第四次驳回）**：world_engine.py 每 Tick 每类型仅写 1 条全量事件 | ✅ |
| 76 | P1: personality 全表更新长锁（第四次提出） | — | **过度优化**：50 行数据瞬时完成 | ✅ |
| 77 | P1: 分区预创建依赖服务重启（v8 P1 #68 延后项） | P1 | 新增 APScheduler，每月 25 号 03:00 自动执行 pre_create_partitions | ✅ |
| 78 | P1: 异步向量化无失败处理（v8 P1 #69 延后项） | P1 | memory_episodes 增 fail_count/last_error 字段 + EmbeddingWorker 熔断逻辑 | ✅ |
| 79 | P2: module_configs updated_at 触发器未覆盖（再次提出） | — | **不成立**：module_configs 表不存在于任何迁移 | ✅ |
| 80 | P2: idx_ar_action/idx_ar_params 索引缺失（再次提出） | — | v8 已补充 | ✅ |
| 81 | 架构：反思层语义检索退化 | 架构 | 延后至 Phase 4 补回 reflections.embedding | ⏳ Phase 4 |
| 82 | 架构：软删除与物理级联语义冲突 | 架构 | 延后至 Phase 4 统一删除策略 | ⏳ Phase 4 |
| 83 | 架构：关系表无双向一致性触发器 | 架构 | 延后至 Phase 4 社交模块阶段 | ⏳ Phase 4 |
| 84 | 架构：事件溯源缺少状态哈希校验闭环 | 架构 | 延后至 Phase 4 可观测性阶段 | ⏳ Phase 4 |
| 85 | 架构：冷热分离无落地机制 | 架构 | 延后至 Phase 4 制定归档流程 | ⏳ Phase 4 |
| 86 | P2: related_characters 类型不统一（action_records JSONB / memory_episodes UUID[]） | P2 | 延后至 Phase 4 统一 | ⏳ Phase 4 |
| 87 | P2: 索引命名规范不统一 | P2 | 延后至 Phase 4 全库统一 | ⏳ Phase 4 |
| 88 | 文档：characters.traits/inventory/status ORM 默认值与 DB 不一致 | 文档 | 已按方案 B 对齐（ORM 层 default，DB nullable） | ✅ |
| 89 | Phase 3: 创建 conversations + messages 表迁移 | Phase 3 | 新增 0003_messages.py 迁移（messages 改为非分区表，对齐 ORM） | ✅ |
| 90 | Phase 3: 创建 ConversationRepository + MessageRepository | Phase 3 | 新增 conversation_repo.py / message_repo.py | ✅ |
| 91 | Phase 3: 创建 MessageService 消息服务层 | Phase 3 | 新增 src/messaging/service.py（含上下文压缩 + LLM 回复生成） | ✅ |
| 92 | Phase 3: 消息 API 路由 | Phase 3 | 新增 /api/v1/messages/send、/history、/stats 与 /api/v1/conversations | ✅ |
| 93 | Phase 3: MCP code-executor 实现 | Phase 3 | 实现沙箱执行（subprocess 隔离 + 模块白名单 + 超时保护） | ✅ |
| 94 | Phase 3: MCP shop-simulator 实现 | Phase 3 | 实现商品目录 + 购买/出售事务校验（24 件默认商品） | ✅ |

> **v9 核心结论**：Phase 3 主体（消息服务 + MCP Server）已完成。P1 延后项（#77 分区调度、#78 向量化失败处理）已落地。剩余架构层面问题统一延后至 Phase 4。

### 新增/修改文件（Phase 3）

| 文件 | 说明 |
|------|------|
| `alembic/versions/0003_messages.py` | Phase 3 迁移：conversations + messages 表 + memory_episodes 失败处理字段 + pre_create_partitions 精简 |
| `src/db/models/memory_episode.py` | 增加 fail_count / last_error 字段，更新 idx_mem_unmaterialized 部分索引 |
| `src/db/models/conversation.py` | Conversation + Message ORM 模型（已存在，v3 增强 tokens/cost 字段） |
| `src/db/repositories/conversation_repo.py` | 会话 Repository（get_or_create 幂等 + 上下文更新 + 列表查询） |
| `src/db/repositories/message_repo.py` | 消息 Repository（游标分页 + token/cost 聚合统计） |
| `src/db/repositories/memory_repo.py` | 新增 mark_embedding_failed 方法，fetch_unmaterialized 排除熔断记忆 |
| `src/memory/embedding_worker.py` | EmbeddingWorker 增强：失败标记 + 熔断检测 + 详细日志 |
| `src/messaging/__init__.py` | 消息服务模块入口 |
| `src/messaging/service.py` | MessageService（用户消息处理 + LLM 回复生成 + 上下文压缩） |
| `src/scheduler/__init__.py` | 调度器模块入口 |
| `src/scheduler/partition_scheduler.py` | PartitionScheduler（APScheduler 月度分区预创建） |
| `src/main.py` | 集成 MessageService API + PartitionScheduler lifespan + admin status |
| `packages/mcp-servers/code-executor/server.py` | MCP 沙箱执行（subprocess + 模块白名单） |
| `packages/mcp-servers/shop-simulator/server.py` | MCP 商店模拟（24 件商品 + 购买/出售事务） |
| `packages/mcp-servers/shop-simulator/pyproject.toml` | shop-simulator 包配置 |
| `pyproject.toml` | 新增 apscheduler>=3.11 依赖 |

---

## 六、Phase 3：外部交互 ✅ 已完成

### 6.1 消息服务

| 任务 | 说明 | 状态 |
|------|------|------|
| OneBot v12 adapter | QQ WebSocket 接入 | ✅ |
| 飞书 adapter | Lark API 接入 | ✅ |
| Web WebSocket | 浏览器接入 | ✅ |
| 回复生成 | LLM 生成自然语言回复 | ✅ |
| 主动分享链路 | 分享意图评估 + 文案生成 + 发送调度 | ✅ |

### 6.2 MCP Servers

| 任务 | 说明 | 状态 |
|------|------|------|
| code-executor（自研） | Python 代码沙箱执行（subprocess 隔离 + 模块白名单） | ✅ |
| shop-simulator（自研） | 商店购买模拟（24 件商品 + 购买/出售事务） | ✅ |
| character-social（自研） | 角色社交（送礼/约会/冲突解决） | ✅ |
| knowledge-base（自研） | 小镇设定库查询（世界规则/角色/场景/行动/记忆） | ✅ |
| web-search（社区） | Tavily API 集成 | ✅ |
| weather（社区） | OpenWeatherMap 集成 | ✅ |
| 模块管理 API | `/api/v1/modules`、`/api/v1/mcp/servers`、`/api/v1/mcp/tools` | ✅ |

---

## 七、Phase 3.5：安全与可靠性 ✅ 已完成

> 基于[项目全面分析](#九项目全面问题分析)发现的安全与一致性问题。

### 7.1 安全加固

| 任务 | 说明 | 验收 |
|------|------|------|
| API 鉴权 | JWT + API Key，管理 API 强制鉴权 | 未授权请求被拒绝 |
| Prompt 注入防护 | 用户消息过滤 + 系统提示隔离 | 注入测试不泄露系统信息 |
| MCP Server 通信加密 | mTLS 或共享密钥 | 抓包看不到明文 |
| 用户消息加密存储 | 敏感字段 AES 加密 | DB 直接查询看不到明文 |

### 7.2 LLM 成本控制

| 任务 | 说明 | 验收 |
|------|------|------|
| 日预算上限 | 超预算自动降级到 flash 模型 | 超预算触发降级 |
| 熔断机制 | LLM 失败率 > 10% 触发熔断 | 熔断后回退到默认 Action |
| Token 计数 | 每次 LLM 调用记录 token 消耗 | Langfuse 可查看成本 |

### 7.3 数据一致性

| 任务 | 说明 | 验收 |
|------|------|------|
| Redis ↔ PG 同步 | character_states 乐观锁 + 定期校验 | 不一致可检测 |
| Action 执行原子性 | PG 事务 + Redis 写入失败补偿 | Redis 写失败自动重试 |
| WebSocket 重连 | 指数退避 + 状态恢复 | 断线 30s 内恢复 |

---

## 八、Phase 4：可观测性与运维 ✅ 已完成

### 8.1 Trace

| 任务 | 说明 | 验收 |
|------|------|------|
| OTel SDK 集成 | `start_span` 埋点 | Jaeger 可查看链路 |
| Trace 采样 | 0.5 采样率 | 采样后仍可追踪关键路径 |
| Langfuse 集成 | LLM Prompt/Token 审计 | Langfuse 可查看对话记录 |

### 8.2 Metrics

| 任务 | 说明 | 验收 |
|------|------|------|
| Prometheus 指标 | Tick 耗时 / Action 成败 / LLM 调用 | Grafana 可查询 |
| 告警规则 | 5xx / Tick 延迟 / LLM 失败率 | 告警触发飞书通知 |

### 8.3 Logs

| 任务 | 说明 | 验收 |
|------|------|------|
| 结构化 JSON 日志 | stdout → stdout | 日志含 trace_id |
| Grafana Alloy 采集 | Docker 容器日志 → Loki | Grafana Logs 面板可查询 |
| Trace ↔ Logs 联动 | trace_id 关联 | Jaeger Span 可跳转 Logs |

---

## 九、Phase 5：前端 Dashboard ✅ 已完成

### 9.1 页面清单

| 页面 | 说明 |
|------|------|
| `/` | 世界总览：时间/天气/活跃角色数 |
| `/characters` | 角色列表：状态/位置/当前行为 |
| `/character/:id` | 角色详情：状态/记忆/计划/关系 |
| `/map` | 小镇地图：场景热力图（拥挤度） |
| `/traces` | Trace 调试：按 trace_id 查链路 |
| `/admin` | 管理入口：暂停/恢复/快照回放/强制 Tick |

### 9.2 技术验收

| 任务 | 验收 |
|------|------|
| React 19 + Compiler | 无手写 useMemo/useCallback |
| ES2024 特性 | 使用 Set.intersection 等 |
| oxlint + oxfmt | lint/format 通过 |
| TanStack Query | WebSocket 实时数据流 |
| Tailwind v4 + 二次元配色 | GlassCard 组件渲染正确 |

---

## 十、项目全面问题分析

> 除数据库审查外，对整个项目架构的全面分析。

### 10.1 架构层面

| 问题 | 严重度 | 影响 | 解决方案 | 阶段 |
|------|--------|------|----------|------|
| World Tick 双主风险 | 高 | 锁过期 + 网络分区导致双实例推进 | Redis Redlock + fencing token | Phase 3.5 |
| Character Tick 无优先级 | 中 | 重要角色可能被低优先级角色阻塞 | 优先级队列调度 | Phase 4 |
| 消息服务单点 | 中 | QQ/飞书 WebSocket 连接无故障转移 | 多实例 + 共享会话状态 | Phase 3.5 |
| Embedding worker 单点 | 低 | worker 宕机导致记忆不向量化 | 多 worker + SKIP LOCKED（已实现） | ✅ 已解决 |

### 10.2 LLM 调用层面

| 问题 | 严重度 | 影响 | 解决方案 | 阶段 |
|------|--------|------|----------|------|
| LLM 超时无 fallback | 高 | Tick 阻塞，角色"卡住" | 强制超时 10s + 默认"等待"回退 | Phase 3.5 |
| Token 成本失控 | 高 | 异常循环烧光预算 | 日预算上限 + 熔断降级 | Phase 3.5 |
| Prompt 注入风险 | 高 | 用户消息操纵角色行为 | 输入过滤 + 系统提示隔离 | Phase 3.5 |
| LLM 决策非法输出 | 中 | Action 执行失败 | 结构化校验 + 回退（已实现） | ✅ 已解决 |

### 10.3 数据一致性

| 问题 | 严重度 | 影响 | 解决方案 | 阶段 |
|------|--------|------|----------|------|
| Redis ↔ PG 双写无事务 | 高 | 状态不一致 | 乐观锁 + 定期校验任务 | Phase 3.5 |
| Action 执行非原子 | 高 | PG 成功 Redis 失败 | Redis 写入失败补偿队列 | Phase 3.5 |
| 分区表无 FK 约束 | 中 | memory_episodes 引用悬空 | 应用层 ORM 保证（已实现） | ✅ 已解决 |
| DEFAULT 分区静默写入 | 中 | 查询性能崩塌 | 触发器告警（已实现） | ✅ 已解决 |

### 10.4 安全性

| 问题 | 严重度 | 影响 | 解决方案 | 阶段 |
|------|--------|------|----------|------|
| 管理 API 无鉴权 | 高 | 任何人可调用 /admin/* | JWT + API Key | Phase 3.5 |
| MCP 通信未加密 | 中 | 内网抓包可篡改 | mTLS | Phase 3.5 |
| 用户消息明文存储 | 中 | DB 泄露暴露隐私 | 敏感字段 AES 加密 | Phase 3.5 |
| CORS 配置过宽 | 低 | 跨站请求伪造 | 限制 origin 白名单 | Phase 3.5 |

### 10.5 前端

| 问题 | 严重度 | 影响 | 解决方案 | 阶段 |
|------|--------|------|----------|------|
| WebSocket 无重连 | 高 | 断线后数据不更新 | 指数退避重连 | Phase 5 |
| 无全局错误边界 | 中 | 组件崩溃白屏 | ErrorBoundary 组件 | Phase 5 |
| 无 loading 骨架屏 | 低 | 用户体验差 | Skeleton 组件 | Phase 5 |

### 10.6 运维

| 问题 | 严重度 | 影响 | 解决方案 | 阶段 |
|------|--------|------|----------|------|
| 数据库迁移非零停机 | 高 | 0002_optimize 表重建需维护窗口 | pg_repack 或蓝绿部署 | Phase 4 |
| Docker 日志未轮转 | 中 | 磁盘撑满 | json-file + max-size | Phase 4 |
| 备份未自动化 | 中 | 人工备份易遗忘 | pg_cron + WAL 归档 | Phase 4 |
| 分区预创建未自动化 | 中 | 月底插入失败 | pg_cron 定时建分区 | Phase 4 |

---

## 十一、里程碑与交付物

| 里程碑 | 预计周期 | 交付物 | 状态 |
|--------|----------|--------|------|
| **M0: 项目骨架** | 1 周 | monorepo + packages + DDL + Docker | ✅ |
| **M1: 世界可运行** | 2 周 | World Tick + Character Tick + Action 10+ | ✅ |
| **M2: 角色有生活** | 1 周 | 角色卡 + 小镇 + 作息 + 关系 | ✅ |
| **M2.5: 性能优化** | 1 周 | 分区 + 异步 embedding + 差分快照 | ✅ |
| **M3: 可外部交互** | 2 周 | QQ/飞书/Web + MCP 6 Server + 模块管理 API | ✅ |
| **M3.5: 安全可靠** | 1 周 | 鉴权 + 成本控制 + 一致性 | ✅ |
| **M4: 可观测完整** | 1 周 | Trace/Metrics/Logs + Langfuse | ✅ |
| **M5: 前端可用** | 2 周 | Dashboard 6 页面 + 实时数据 + ErrorBoundary + WebSocket 重连 | ✅ |

**总预计周期**：11 周（约 2.5 个月）

---

## 十二、风险与依赖

| 风险 | 影响 | 缓解措施 | 状态 |
|------|------|----------|------|
| pg_uuidv7 扩展未安装 | UUID v4 索引碎片化 | 应用层 `uuid6` 库兜底 | ✅ 已缓解 |
| LLM 调用超时/失败 | Tick 阻塞 | 强制超时 + 重试 + 默认"等待"回退 | ✅ 已解决 |
| PgBouncer prepared statements | 与事务模式冲突 | `DB_PREPARED_STATEMENT_CACHE_SIZE=0` | ✅ 已缓解 |
| MCP Server 不稳定 | 工具调用失败 | 超时 + fallback + 告警 | ✅ 已解决 |
| 角色 Tick 并发过多 | 资源耗尽 | 信号量限制 + 优先级调度 | ✅ 部分（信号量已有） |
| HNSW 召回率崩塌 | 记忆检索空结果 | HASH 分区（16）+ 父表 HNSW | ✅ 已解决 |
| Embedding 阻塞 Tick | 角色卡顿 | 异步 worker + materialized 标志 | ✅ 已解决 |
| world_snapshots IO 灾难 | WAL 膨胀 | 差分事件表 + 定期快照（每 1000 Tick）+ 事件去重 | ✅ 已解决 |
| 冷启动恢复时间线性增长 | 服务重启慢 | 快照 + 增量事件回放（启动时间恒定） | ✅ 已解决 |
| character_states 表膨胀 | 索引膨胀 + VACUUM 压力 | fillfactor=85 + autovacuum 调优 | ✅ 已解决 |
| reflection_sources 悬空引用 | 脏数据 | 复合外键 ON DELETE CASCADE | ✅ 已解决 |
| 分区表无 FK → 孤儿记忆 | 查询异常 | DB 外键 REFERENCES characters(id) ON DELETE CASCADE（v4 修复） | ✅ 已解决 |
| 分区忘记预创建 → 月初写入失败 | 服务中断 | pre_create_partitions() 函数自动预创建 | ✅ 已解决 |
| world_events 重复写入 → 回放状态错误 | 状态异常 | UNIQUE(tick_id, event_type) + ON CONFLICT DO NOTHING（v4 修复） | ✅ 已解决 |
| VACUUM FULL 阻塞全表 | 服务中断 | 移除自动执行，改为手动维护（v4 修复） | ✅ 已解决 |
| DEFAULT 分区静默丢数据 | 数据丢失 | 删除前检查数据量（v4 修复） | ✅ 已解决 |
| 文档与代码脱节导致开发误用 | 业务代码报错 | 方案 B 全文对齐 data-model.md（v5 修复） | ✅ 已解决 |
| reflections.related_episodes 双写不一致 | 数据脏写 | 删除废弃字段，统一走 reflection_sources（v5 修复） | ✅ 已解决 |
| messages 表索引在表不存在时创建 → 迁移中断 | 上线即失败 | 移除索引创建，表+索引推迟到 Phase 3（v6 修复） | ✅ 已解决 |
| memory_episodes 大表重建卡死 | 服务长时间不可用 | 显式 statement_timeout + lock_timeout（v6 修复） | ✅ 已解决 |
| 0001_init action_records 未声明分区 + embedding 类型错误 | 迁移链从未成功执行 | 原生 SQL 重建分区表 + vector(1536) 类型（v7 修复） | ✅ 已解决 |
| 0002_optimize JSONB→UUID[] 隐式转换失败 | 数据迁移中断 | 显式 jsonb_array_elements_text 转换（v8 修复） | ✅ 已解决 |
| Redis ↔ PG 不一致 | 状态漂移 | 乐观锁 + 校验任务 | ✅ 已解决 |
| LLM 成本失控 | 预算超支 | 日预算 + 熔断降级 | ✅ 已解决 |
| 跨角色全局向量检索性能崩塌 | 全局搜索慢 | 额外维护全局非分区向量索引（未来需求） | ⏳ Phase 4+ |
| 分区表统计信息漂移 | 执行计划劣化 | 配置更频繁的自动分析阈值 | ⏳ Phase 4 |
| 向量索引碎片化 | 召回率下降 | 定期监控 + 低峰期索引重建 | ⏳ Phase 4 |
| world_events 未分区 | 高频写入表膨胀 | 按月 RANGE 分区（待数据量达标后实施） | ⏳ Phase 4 |
| conversations/messages 表缺迁移 | 消息服务无表可用 | Phase 3 消息服务阶段补建迁移脚本 | ✅ 已解决 |
| related_characters 类型不统一（JSONB vs UUID[]） | 查询模式不一致 | 评估查询模式后统一类型 | ⏳ Phase 4 |
| 软删除 vs 物理级联语义混乱 | 数据残留/误删 | 制定全表一致的删除策略 | ⏳ Phase 4 |

---

## 十三、下一步行动

所有 Phase 0-5 已全部完成。后续优化方向：

1. ~~**生产部署**：Docker Compose 编排 + Nginx 反向代理 + HTTPS~~ ✅ 已完成（详见 [Docker 部署指南](docker-deployment.md)）
2. ~~**Grafana 统一面板**：集成 Prometheus + Loki + Jaeger 数据源~~ ✅ 已完成（3 个预置 Dashboard + Trace↔Logs 联动）
3. ~~**MCP 插件单独开关**：前端 Dashboard 控制~~ ✅ 已完成（Redis 持久化 + toggle UI）
4. ~~**LLM 记忆重要程度评分**：`MEMORY_LLM_SCORING_ENABLED` 开关~~ ✅ 已完成
5. ~~**前端监控页面集成**：`/monitoring` + `/admin/logs` + `/admin/metrics-detail`~~ ✅ 已完成
6. **告警规则**：5xx 错误率 / Tick 延迟 / LLM 失败率 → 飞书通知
7. **Docker 日志轮转**：json-file + max-size 防磁盘撑满
8. **数据库备份自动化**：pg_cron + WAL 归档
9. **world_events 按月分区**：待数据量达标后实施
10. **跨角色全局向量检索**：维护全局非分区向量索引（未来需求）

---

## 十四、相关文档

| 主题 | 文档 |
|------|------|
| 架构总览 | [architecture.md](architecture.md) |
| 开发指南 | [development-guide.md](development-guide.md) |
| 数据模型 | [data-model.md](data-model.md) |
| 部署 | [deployment.md](deployment.md) |
| Docker 部署 | [docker-deployment.md](docker-deployment.md) |
| 项目不足审查 | [gap-analysis.md](gap-analysis.md) |
