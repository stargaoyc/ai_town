# Changelog

本文件记录面向使用者的显著变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。项目当前处于快速迭代期（0.x），
破坏性变更可能在 minor 版本间发生。

## [Unreleased]

### Added

- **多模型备用源**：`LLM_FALLBACK_SOURCES` 可配置多个 OpenAI-compatible 源，调用失败自动切换，失败源冷却 5 分钟后作为末位兜底。
- **Redis vs PG 状态对账**：后台每 10 分钟 diff 两库并自动修复（键缺失回灌、字段漂移以 Redis 为准修正 PG）；新增指标 `ai_town_reconcile_drift_total` / `ai_town_reconcile_repair_total`。
- **Prometheus 告警规则**：11 条规则覆盖世界 Tick 停摆、角色 Tick 失败率、LLM 预算/熔断、状态漂移、Redis 断连、5xx 错误率。
- **`/ws/dashboard` 实时推送**：登录后订阅仪表盘帧（世界状态 + 通知未读数，每 5 秒），前端轮询降为 30 秒断连兜底。
- **openapi-typescript 类型生成管道**：`pnpm gen:api` 从后端 OpenAPI spec 生成契约类型。
- **集成测试基座**：`tests/integration/` 连真实 PostgreSQL + Redis（独立 `ai_town_it` 库经 alembic 迁移重建），服务不可达自动跳过；覆盖 Repository 层、分布式锁、状态对账。

### Changed

- Docker 编排合并为单一 `docker-compose.yml`（原 `docker-compose.infra.yml` / `docker-compose-win.infra.yml` 删除）；PG 统一为 `pgvector/pgvector:pg18` 官方镜像、端口 5433、bind mount `./data/`。
- 消息分页改为 `(created_at, id)` keyset 双游标，修复同事务批量写入时游标漏数据；Action 时间线查询补 UUIDv7 tiebreaker。
- `EMBEDDING_DIM` 默认值对齐迁移 0005 的物理列 `halfvec(2048)`（此前默认配置下 embedding 写入必失败）。
- 场景拥挤度特性接通：角色移动时维护 `world:scene:visitors` 计数（此前恒为 0）。
- `/api/v1/actions/{id}` 返回真实 Action 字段（scene / allow_dynamic_duration / 全部带符号 cost 字段）。

### Removed

- Lark（飞书）适配器（从未挂载路由）；`platform` 枚举保留 `lark` 值供历史数据。
- 前端死代码 `useWebSocket.ts`（由 `useDashboardSocket` 取代）。
- main.py 平行模块级全局实例与 notifications API 的重复实现（runtime 容器为唯一注册表）。

## [0.2.0] - 2026-08-22

### Added

- 第二阶段核心架构修复：角色 Tick 并发化（gather + 信号量）、Action executor 抽象落地、12 个内嵌 Prompt 全部外置到 `configs/prompts/*.yaml`（缺失即 fail-fast）、双配置体系统一（Settings 为唯一默认值真相源）。
- 分布式锁加固：唯一 token + Lua compare-and-delete/expire + 看门狗续租。
- 真实 token usage 贯通：预算扣减、消息持久化、指标上报统一使用 response_metadata 真实值与统一单价表。
- 消息服务加固：OneBot 消息 Redis SETNX 去重、群聊回复概率常量化单点判定、Web 分享推送 UUID/str 类型 bug 修复、扇出去重 + 后台投递。
- 启动回灌（P0-3）：PG 镜像 → Redis 缺失键。
- 工具 delta 纳入 PG 事务（P0-1）；Redis 状态统一编解码器 `state_codec.py`（P0-2）。

### Security

- WebSocket 握手 JWT 鉴权；PUBLIC_GET 白名单收敛；RBAC 默认角色改 viewer；限流器 Lua 原子化。

[Unreleased]: https://github.com/stargaoyc/ai_town/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/stargaoyc/ai_town/compare/v0.1.0...v0.2.0
