# 项目不足审查与改进路线图

> 本文档对 `e:\projects\aitown`（二次元 AI 小镇陪伴智能体）项目进行全面不足审查，并结合参考项目 `e:\projects\yuiju`（TypeScript 实现的同类多智能体系统）的工程亮点，给出可执行的改进建议与路线图。
>
> - 审查基线：aitown `packages/backend` 后端代码与 `packages/frontend` 前端代码、`docs/` 文档体系、`docker/` 与 `docker-compose.yml` 部署编排
> - 参考项目：yuiju monorepo（`packages/world`、`packages/message`、`packages/utils`、`packages/web`、`apps/site`）
> - 撰写日期：2026-07-12
> - 更新日期：2026-07-14（标注已完成项 ✅）

## 零、改进进度总览（2026-07-14 更新）

| 改进项               | 优先级 | 状态        | 说明                                                                                                                                                          |
| -------------------- | ------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CI/CD 流水线         | P0     | ✅ 已完成   | `.github/workflows/ci.yml` 含后端(ruff+pytest)、前端(lint+typecheck+build)、MCP Servers 检查                                                                  |
| AGENTS.md            | P0     | ✅ 已完成   | 根目录 `AGENTS.md` 定义 AI Coding 执行协议、项目约束、验证命令                                                                                                |
| docs/rules/ 规范体系 | P0     | ✅ 已完成   | 4 套规范文档（implementation-style/domain-design-style/prompt-style/refactor-style）                                                                          |
| main.py 拆分         | P1     | ✅ 已完成   | 从 3111 行降至 541 行，拆分到 `src/api/` 11 个路由模块                                                                                                        |
| 全局变量反模式消除   | P1     | ✅ 已完成   | 创建 `src/runtime.py` 依赖容器，`from src.main import` 已清零                                                                                                 |
| 全局异常处理中间件   | P1     | ✅ 已完成   | `src/api/exceptions.py` 统一错误响应格式 + trace_id                                                                                                           |
| Prompt 深度增强      | P0     | ✅ 已完成   | chat/decision/reflection 三个 YAML 全面重写，含人设分层/状态泄露防护/跨世界边界/真实感三原则                                                                  |
| 多智能体交互         | P1     | ✅ 已完成   | chat_with Action 完整实现：场景感知→LLM 对话生成→双向关系更新→双记忆持久化                                                                                    |
| 前后端同步           | P1     | ✅ 已完成   | NearbyCharacter 接口+钩子+UI、ActionEntry 类型修复、actions 页面对话展示                                                                                      |
| Pre-commit hooks     | P0     | ✅ 已完成   | `.pre-commit-config.yaml` 纯本地 hooks（ruff/oxlint/tsc/check-yaml/check-merge-conflict）                                                                     |
| 安全底线置顶         | P0     | ✅ 已完成   | chat.yaml 拆分 system_template（SystemMessage）+ template（HumanMessage），安全约束优先级最高；proactive_sharing/character_tick 同步接入 SAFETY_SYSTEM_PROMPT |
| Person Memory        | P2     | ✅ 已完成   | 修复 dict(row._mapping) TypeError、LLM 异步更新记忆、API 端点修复                                                                                             |
| 日记系统             | P2     | ✅ 已完成   | DiaryService 自动调度（30 分钟检查，日/周/月/年日记生成）                                                                                                     |
| RBAC 权限系统        | P2     | ✅ 已完成   | JWT 含 role claim、rbac_roles 配置支持多角色（admin/operator/viewer）                                                                                         |
| JWT 安全增强         | P2     | ✅ 部分完成 | 新增 jti claim 支持黑名单；默认密码启动警告；refresh_token 待实现                                                                                             |
| .env.example 同步    | P1     | ✅ 已完成   | OTEL_ENDPOINT 修正、时间对齐文档、world_tick_minutes 支持 float                                                                                               |
| 角色导入冲突更新     | P1     | ✅ 已完成   | 同名角色导入改为 update_from_dict（保留历史数据），不再返回 409                                                                                               |
| langgraph 清理       | P2     | ✅ 已完成   | 从 pyproject.toml 移除直接依赖（作为 langchain 传递依赖保留）                                                                                                 |
| MCP 改进分析         | P2     | ✅ 已完成   | `docs/mcp-improvement-analysis.md` 213 行分析文档，含工具归属判断和迁移路径                                                                                   |
| WORLD_TICK_ID 修复   | P1     | ✅ 已完成   | 启动时同步 Gauge 指标，避免重启后指标面板显示 #0                                                                                                              |
| CONTRIBUTING.md      | P0     | ❌ 未完成   |                                                                                                                                                               |
| 多模型备用源         | P1     | ❌ 未完成   | LLM 仍为单一 endpoint                                                                                                                                         |
| 核心模块测试覆盖     | P1     | ❌ 未完成   | WorldEngine/CharacterTickEngine/MessageService 仍无测试                                                                                                       |
| 速率限制覆盖         | P2     | ❌ 未完成   |                                                                                                                                                               |
| 模块边界重组         | P2     | 📋 已规划   | core/ → core/world/ + core/character/ 重组方案已文档化，待执行                                                                                                |
| 跨角色资源原子化     | P2     | 📋 已规划   | per-character 锁已有，跨资源锁待实现                                                                                                                          |

---

## 一、审查方法论

### 1.1 审查范围

| 维度     | 范围                                                         | 关键路径                                                                                 |
| -------- | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| 架构设计 | `packages/backend/src/` 全部模块组织、依赖关系、模块边界     | `src/main.py`、`src/core/`、`src/memory/`、`src/messaging/`、`src/adapters/`、`src/mcp/` |
| 工程质量 | 类型安全、测试覆盖、错误处理、代码规范、日志规范             | `pyproject.toml`、`tests/`、`src/observability/logging.py`                               |
| 文档体系 | `docs/` 17 个文档 + `README.md` + API 文档 + ADR + CHANGELOG | `docs/*.md`                                                                              |
| DevOps   | CI/CD、Docker 化、环境管理、监控告警                         | `.github/workflows/`（缺失）、`Dockerfile`、`docker-compose.yml`、`.env.example`         |
| 安全     | 认证授权、输入验证、密钥管理、速率限制                       | `src/auth/`、`src/security/`、`src/config.py`                                            |
| 对标差距 | 与 yuiju 项目在规范、配置、部署、记忆系统等维度的差距        | `e:\projects\yuiju\AGENTS.md`、`docs/rules/`、`yuiju.config.ts`、`packages/utils/src/`   |

### 1.2 评判标准

- **架构合理性**：模块边界是否清晰、依赖方向是否单向、是否存在循环依赖风险、配置真相源是否唯一
- **工程严谨度**：是否有 CI 自动化、类型检查是否强制、测试是否有意义覆盖（不只是happy path）、错误处理是否统一
- **可维护性**：文档是否完整且与代码同步、是否有贡献指南、是否有 ADR 记录关键决策
- **安全性**：鉴权是否完备、密钥是否有轮换机制、输入是否有验证、限流是否覆盖关键端点
- **可观测性**：日志是否结构化、Trace 是否全链路、指标是否覆盖关键路径、是否有告警规则

### 1.3 参考项目说明

yuiju 是一个 TypeScript 实现的同类多智能体陪伴系统，采用 monorepo 结构（`packages/world` + `packages/message` + `packages/utils` + `packages/web` + `apps/site`），核心特征：

- Redis 作为角色实时状态真相源，MongoDB 保存行为历史与记忆
- 通过 `yuiju.config.ts` 单一配置源管理全部业务配置
- 拥有完善的 AI Coding 规范体系（`AGENTS.md` + `docs/rules/*.md`）
- 模型多源备用 + 冷却机制（`packages/utils/src/llm/models.ts`）
- 记忆系统分层清晰（`person-memory/` 分 directory/format/heat/storage/types/update 六个文件）
- 日记系统作为 Episode 之上的叙事归档层
- VitePress 文档站 + Biome 统一工具链 + Husky pre-commit + GitHub Actions CI

---

## 二、架构层面不足

### 2.1 目录组织

#### 2.1.1 ✅ `src/main.py` 拆分已完成（原 3111 行 → 现 541 行）

**原问题**：`packages/backend/src/main.py` 单文件达到 3111 行，承担了全部路由、中间件、配置管理、日志解析等职责。

**已完成改进**：

- `main.py` 从 3111 行降至 **541 行**，仅保留 FastAPI 应用初始化、lifespan 生命周期管理、中间件注册、路由 include
- 创建 `src/api/` 目录，拆分为 **11 个路由模块**：`characters.py`、`world.py`、`town.py`、`actions.py`、`messages.py`、`system.py`、`admin.py`、`mcp.py`、`notifications.py`、`memory.py`、`exceptions.py`
- 每个模块使用 `APIRouter(prefix=..., tags=...)` 组织，`main.py` 通过 `app.include_router()` 注册
- 全局异常处理器独立到 `src/api/exceptions.py`，统一错误响应格式 + trace_id
- 依赖容器抽离到 `src/runtime.py`，消除 `from src.main import` 反模式

#### 2.1.2 ✅ `src/api/` 路由层目录已创建

**已完成**：`src/api/` 目录已创建，包含 11 个路由模块，使用 `APIRouter` 聚合后 `app.include_router()` 注册。文档与代码已一致。

### 2.2 模块边界

#### 2.2.1 `core/` 模块职责边界模糊

**现状**：`src/core/` 同时包含：

- `world_engine.py`（世界引擎）
- `character_tick.py`（角色 Tick 引擎）
- `evolutions/` 子目录（5 个演化器：event/resource/scene/time/weather）

**问题**：

- `character_tick.py` 单文件超过 700 行（含 5 阶段闭环逻辑），与 `world_engine.py` 在同一层级但职责差异大
- `evolutions/` 是 World Engine 的子能力，放在 `core/` 顶层而非 `core/world/` 子目录，导致 `core/` 目录看起来像"杂项核心逻辑堆放处"
- 缺少 `core/action_system.py`（`development-guide.md` 文档中提到的文件实际不存在，Action 系统实际在 `src/actions/` 目录）

**改进建议**：

- 重组为 `src/core/world/`（含 `engine.py` + `evolutions/`）和 `src/core/character/`（含 `tick.py` + 阶段处理器）
- 修正 `development-guide.md` 中与代码不一致的路径描述

#### 2.2.2 `modules/` 与 `core/` 边界不清

**现状**：`src/modules/` 包含 `character/`、`duration/`、`movement/`、`relation/`、`schedule/`、`town/`，但这些模块大多是被 `core/` 调用的辅助系统，而非可插拔模块。

**问题**：

- 命名上 `modules/` 暗示"可插拔模块"，实际是核心子系统（移动、作息、关系）
- 真正的可插拔能力是 `mcp-servers/`，但那是在 `packages/` 层级，不在 `src/modules/`
- `src/modules/character/importer.py` 和 `src/modules/character/schema.py` 与 `src/db/models/character.py` 职责重叠

**改进建议**：将 `modules/` 重命名为 `systems/` 或按领域归并到 `core/` 子目录；明确"可插拔模块"特指 MCP Server。

### 2.3 依赖管理

#### 2.3.1 ✅ 全局变量 + `from src.main import` 反模式已消除

**原问题**：`main.py` 通过模块级全局变量持有所有运行时实例，6 个模块通过 `from src.main import` 延迟获取这些全局变量，形成隐式循环依赖。

**已完成改进**：

- 创建 `src/runtime.py` 作为依赖容器，集中持有 `redis`、`llm`、`prompts`、`ws_manager`、`onebot_adapter`、`movement_system`、`schedule_system` 等运行时实例
- 提供 `get_redis()`、`get_llm()`、`get_prompts()`、`get_ws_manager()`、`get_onebot_adapter()` 等访问函数
- `main.py` 的 `lifespan` 初始化后写入 `src/runtime.py`
- `adapters/`、`messaging/`、`core/` 全部改为从 `src/runtime.py` 导入
- `grep -r "from src.main import" src/` **零结果**，循环依赖彻底消除

#### 2.3.2 `jwt_handler.py` 模块级单例在导入时读取 settings

**问题**：`src/auth/jwt_handler.py` 第 86-90 行在模块导入时立即实例化 `_handler`：

```python
_handler = JWTHandler(
    secret=settings.jwt_secret,
    algorithm=settings.jwt_algorithm,
    expire_hours=settings.jwt_expire_hours,
)
```

**风险**：

- `settings` 在 `config.py` 末尾也是模块级实例化（`settings = Settings()`），但运行时通过 Redis 覆盖 `settings.jwt_secret` 不会生效（因为 `_handler` 已用旧值构造）
- 单元测试必须先设置环境变量再导入，`conftest.py` 已经为此设置占位值（第 6-12 行）

**改进建议**：改为延迟初始化或工厂函数 `get_jwt_handler()`，从 `settings` 读取最新值。

### 2.4 配置管理

#### 2.4.1 `config.py` + Redis 运行时覆盖的混合策略问题

**现状**：

- `src/config.py` 使用 `pydantic-settings` 从 `.env` 加载静态配置（90+ 字段）
- `main.py` 的 `lifespan` 在启动时从 Redis `config:overrides` 读取 JSON，通过 `setattr(settings, key, value)` 覆盖（第 138-151 行）
- `PUT /api/v1/admin/config` 端点允许通过 API 修改白名单配置项（`_RUNTIME_CONFIG_KEYS`，12 个键）

**问题**：

- **真相源不明确**：同一配置项有 `.env` 默认值、Redis 覆盖值、`settings` 内存值三份，`reset_config_item` 端点通过 `Settings()` 重新实例化获取"默认值"（第 2925 行），但 `Settings()` 会再次读取 `.env`，行为依赖运行时 `.env` 文件状态
- **覆盖范围有限**：仅 12 个配置项可通过 Redis 覆盖，但 `jwt_secret`、`openai_api_key` 等敏感配置无法热更新，密钥轮换必须重启
- **类型安全缺失**：`setattr(settings, key, value)` 绕过了 Pydantic 校验，`_RUNTIME_CONFIG_KEYS` 手动维护类型映射，与 `Settings` 类定义重复
- **多实例不一致**：若部署多实例，每个实例在启动时读取 Redis 覆盖，但运行时 `PUT /api/v1/admin/config` 只更新当前实例的 `settings` 内存对象，其他实例需重启才生效

**改进建议**：

- 短期：将运行时配置项收敛到独立的 `RuntimeConfig` Pydantic 模型，从 Redis 加载并校验类型，避免 `setattr` 绕过校验
- 中期：所有需要热更新的配置（如 `character_tick_seconds`、`share_daily_limit`）改为通过 `RuntimeConfig` 单例读取，业务代码每次读取最新值，而非依赖 `settings.character_tick_seconds`
- 长期：参考 yuiju 的 `yuiju.config.ts` 单一配置源模式，将业务配置集中到一个 TypeScript/Python 配置文件，`.env` 仅保留敏感密钥

#### 2.4.2 `.env.example` 缺少部分配置项

**问题**：`.env.example` 与 `config.py` 中的 Settings 字段不完全对应：

- `config.py` 有 `embedding_model_key`、`embedding_model_url`、`langfuse_host`、`langfuse_public_key`、`langfuse_secret_key`、`mcp_tool_timeout`、`onebot_self_id`、`onebot_group_at_only`、`onebot_group_character_map` 等字段，`.env.example` 中未列出或缺失
- `.env.example` 中有 `DB_PREPARED_STATEMENT_CACHE_SIZE`，但 `config.py` 的 `Settings` 类未定义该字段（`extra="ignore"` 导致被静默忽略）
- `.env.example` 中有 `ONE_BOT_WS_URL`、`LARK_APP_ID`、`WEB_WS_PATH`，但 `config.py` 中均未定义

**改进建议**：同步 `.env.example` 与 `Settings` 字段，移除未使用项，补充缺失项；考虑用脚本自动从 `Settings` 生成 `.env.example` 模板。

---

## 三、工程质量不足

### 3.1 类型安全

#### 3.1.1 mypy 配置存在但未强制

**现状**：`pyproject.toml` 第 76-79 行配置了 mypy strict 模式：

```toml
[tool.mypy]
python_version = "3.13"
strict = true
ignore_missing_imports = true
```

**问题**：

- 代码中存在大量 `# type: ignore` 注释（仅 `main.py` 就有 10+ 处，如第 78、86、201、596 行），`character_tick.py` 中 `from src.main import ws_manager as _ws_mgr` 后类型变为 `Any`
- 没有 CI 强制执行 `mypy`，配置形同虚设
- `LLMClient` 类中 `chat_llm: ChatOpenAI` 的 `ainvoke` 返回 `BaseMessage`，但代码用 `response.content` 直接访问，类型为 `str | list[ContentBlock]`，实际处理依赖运行时判断

**改进建议**：

- 在 CI 中加入 `mypy` 检查步骤
- 逐步消除 `# type: ignore`，每处要么修正类型要么用 `# type: ignore[specific-error]` 明确原因
- 为 `LLMClient.chat` 等方法补充返回类型标注并保证实现一致

#### 3.1.2 前端类型生成依赖 OpenAPI 但未在 CI 强制

**现状**：`development-guide.md` 提到 `pnpm gen:api` 从后端 OpenAPI 生成类型，但没有 CI 检查确保生成的类型与后端实际接口同步。

**改进建议**：CI 中加入"重新生成类型 + git diff 检查"步骤，确保 PR 中后端接口变更必须同步前端类型。

### 3.2 测试覆盖

#### 3.2.1 测试覆盖率低且偏科严重

**现状**：`packages/backend/tests/` 共 16 个测试文件：

| 测试文件                                            | 覆盖范围      |
| --------------------------------------------------- | ------------- |
| `test_actions_base.py`、`test_actions_registry.py`  | Action 系统   |
| `test_api_keys.py`、`test_jwt_handler.py`           | 鉴权          |
| `test_budget_manager.py`、`test_circuit_breaker.py` | 成本控制      |
| `test_duration_calculator.py`                       | 动态耗时      |
| `test_llm_availability.py`                          | LLM 客户端    |
| `test_observability_*.py`（4 个）                   | 可观测性      |
| `test_onebot11.py`                                  | OneBot 适配器 |
| `test_prompt_guard.py`                              | Prompt 防护   |
| `test_weather_evolution.py`                         | 天气演化      |

**问题**：

- **核心模块无测试**：`src/core/world_engine.py`、`src/core/character_tick.py`、`src/messaging/service.py`、`src/memory/episode_service.py`、`src/memory/retrieval_service.py`、`src/memory/reflection_service.py` 等核心业务模块均无单元测试
- **无 API 层测试**：40+ 个 API 端点没有任何 e2e 或集成测试覆盖
- **无数据库层测试**：`src/db/repositories/` 的 10+ 个 Repository 类无测试，SQL 正确性依赖运行时验证
- **无 MCP 集成测试**：6 个 MCP Server 的工具调用链路无测试
- **测试类型偏科**：主要是纯函数单元测试（如 `DurationCalculator`、`PromptGuard`），缺少涉及外部依赖（PG/Redis/LLM）的集成测试
- `conftest.py` 仅提供 3 个 fixture（`sample_state`、`registry`、`duration_calculator`），缺少数据库/Redis mock fixture

**改进建议**：

- 优先补充 `MessageService.handle_user_message` 的集成测试（使用 testcontainers 启动 PG + Redis，`pyproject.toml` 已声明 `testcontainers>=4.9` 但未使用）
- 为每个 Repository 补充 CRUD 测试
- 为关键 API 端点（`/messages/send`、`/admin/tick`、`/admin/config`）补充 e2e 测试
- 引入 `pytest-cov`（已声明依赖）并在 CI 中强制覆盖率阈值（建议初始 40%，逐步提升到 70%）

#### 3.2.2 ✅ CI 配置已创建

**已完成**：`.github/workflows/ci.yml` 已创建，包含三个 job：

- **backend**：`uv sync` → `ruff check` → `ruff format --check` → `pytest`（含 PG + Redis 服务容器）
- **frontend**：`pnpm install` → `oxlint` → `tsc --noEmit` → `pnpm build`
- **mcp-servers**：矩阵构建 4 个 MCP Server 的导入检查

在 push/PR 到 main/master 分支时自动触发。

### 3.3 错误处理

#### 3.3.1 ✅ 全局异常处理中间件已实现

**原问题**：FastAPI 默认异常处理丢失调试信息，多个端点直接暴露 `str(e)` 给客户端。

**已完成改进**：

- 创建 `src/api/exceptions.py`，注册 `@app.exception_handler(Exception)` 全局处理器
- 区分 `HTTPException`（透传 status_code + detail）、`ValueError`/`TypeError`（返回 400）、其他异常（返回 500）
- 异常响应统一格式：`{"detail": "...", "trace_id": "...", "error_code": "..."}`
- 内部异常不暴露 `str(e)`，返回通用 "Internal server error" + trace_id 供排查
- 未知异常记录完整堆栈（`exc_info=True`）

#### 3.3.2 `lifespan` 中异常处理策略不一致

**问题**：`lifespan` 函数中各模块初始化的异常处理策略不统一：

- Redis 初始化失败：`raise`（中断启动）
- LLM 初始化失败：`raise`（中断启动）
- Action Registry 初始化失败：`raise`（中断启动）
- Embedding Worker 启动失败：`embedding_worker = None`（继续启动）
- 分区调度器启动失败：`partition_scheduler = None`（继续启动）
- World Engine 启动失败：`raise`（中断启动）
- Character Tick Engine 启动失败：`character_engine = None`（继续启动）
- Phase 2 模块初始化失败：继续启动
- OneBot 适配器启动失败：继续启动

**问题**：哪些模块是"必须"哪些是"可选"没有明确文档，初始化失败后服务处于降级状态但 `/health` 端点不反映降级状态。

**改进建议**：

- 定义"必须模块"与"可选模块"清单，文档化降级策略
- `/health` 端点返回各模块状态，前端可根据状态显示降级提示
- 引入 `lifespan` 阶段化检查：必须模块失败立即退出，可选模块失败记录但继续

### 3.4 代码规范

#### 3.4.1 ruff 配置存在但未强制

**现状**：`pyproject.toml` 第 68-74 行配置了 ruff：

```toml
[tool.ruff]
line-length = 120
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = ["E501"]
```

**问题**：

- 没有 pre-commit hook 强制执行 `ruff check`，开发者可以提交未通过 lint 的代码
- `ignore = ["E501"]` 关闭了行长度检查，但 `line-length = 120` 已设置，矛盾
- 前端使用 `oxlint.json` 但同样没有 pre-commit

**对比**：yuiju 使用 `.husky/pre-commit` 执行 `pnpm run lint-staged` + `pnpm run type-check`，确保提交前必须通过检查。

**改进建议**：

- 引入 `pre-commit` 框架（Python 生态的 pre-commit，与 yuiju 的 Husky 不同但作用相同）
- 配置 `.pre-commit-config.yaml`，至少包含 `ruff check --fix`、`ruff format`、`mypy`（仅对变更文件）
- 或在 `pyproject.toml` 中配置 `pytest` + `ruff` 的组合 hook

#### 3.4.2 缺少代码风格文档

**问题**：`docs/development-guide.md` 仅描述了目录结构，没有代码风格规范、重构规则、领域设计规范。

**对比**：yuiju 有 `docs/rules/` 目录，包含 4 套规范：

- `implementation-style.md`：代码风格（主流程优先、少加概念、单一真相源、显式边界、少量重复优于错误抽象、注释解释约束）
- `domain-design-style.md`：领域设计规范（限界上下文、分层落点、真相源与副作用、常见修改清单）
- `prompt-style.md`：Prompt 规范（视角、人设与任务、工程概念、维护位置）
- `refactor-style.md`：重构规则

**改进建议**：参考 yuiju 创建 `docs/rules/` 目录，针对 Python 生态和 aitown 的领域模型编写规范文档。

### 3.5 日志规范

#### 3.5.1 structlog 使用基本规范但缺少级别规范文档

**现状**：`src/observability/logging.py` 实现了 structlog 配置：

- 共享 processor chain（`merge_contextvars` + `add_log_level` + `TimeStamper` + `add_trace_context` + `StackInfoRenderer` + `format_exc_info`）
- 支持 JSON 与 Console 双格式
- 文件 + stderr 双输出
- OTel trace_id 注入

**问题**：

- 没有日志级别使用规范文档（何时用 debug/info/warning/error/critical）
- 代码中存在 `logger.warning("runtime_config_override_load_failed", error=str(e))` 和 `logger.error("redis_connection_failed", error=str(e), exc_info=True)` 等合理用法，但也有 `logger.info("rate_limit_exceeded", ...)` 这种应该用 warning 的场景
- 部分日志缺少关键上下文：`logger.error("force_tick_failed", character_id=str(char.id), error=str(e))` 没有 `exc_info=True`，丢失堆栈

**改进建议**：

- 在 `docs/observability.md` 中补充日志级别使用规范
- 统一 `error` 级别日志必须包含 `exc_info=True`
- 引入日志 lint 规则（如 `flake8-logging`）自动检查

---

## 四、文档体系不足

### 4.1 文档完整性

#### 4.1.1 现有 17 个文档清单

| 文档                   | 主题        | 状态                 |
| ---------------------- | ----------- | -------------------- |
| `architecture.md`      | 总体架构    | 完整                 |
| `character-design.md`  | 角色设计    | 完整                 |
| `town-design.md`       | 小镇设计    | 完整                 |
| `world-engine.md`      | 世界引擎    | 完整                 |
| `action-system.md`     | Action 系统 | 完整                 |
| `memory-system.md`     | 记忆系统    | 完整                 |
| `module-system.md`     | 模块与 MCP  | 完整                 |
| `messaging-service.md` | 消息服务    | 完整                 |
| `data-model.md`        | 数据模型    | 完整                 |
| `api-spec.md`          | API 设计    | 完整但可能滞后       |
| `config-reference.md`  | 配置参考    | 完整                 |
| `frontend-design.md`   | 前端设计    | 完整                 |
| `observability.md`     | 可观测性    | 完整                 |
| `deployment.md`        | 部署运维    | 完整                 |
| `development-guide.md` | 开发指南    | 部分内容与代码不一致 |
| `getting-started.md`   | 新手指南    | 完整                 |
| `roadmap.md`           | 路线图      | 完整                 |

#### 4.1.2 缺失的主题

| 缺失文档                  | 重要性 | 说明                                                                   |
| ------------------------- | ------ | ---------------------------------------------------------------------- |
| `CONTRIBUTING.md`         | 高     | 无贡献指南，外部贡献者不知道如何提交 PR、代码规范、测试要求            |
| `CHANGELOG.md`            | 高     | 无变更日志，版本迭代无法追踪                                           |
| `SECURITY.md`             | 高     | 无安全策略，发现漏洞时无报告渠道                                       |
| `docs/adr/` 架构决策记录  | 中     | 无 ADR，关键决策（如为什么选 LangGraph、为什么用 PG 而非 Mongo）无记录 |
| `docs/llm-contract.md`    | 中     | 无 LLM 协定文档，LLM 能做什么、不能做什么、Prompt 维护位置不明确       |
| `docs/runbook.md`         | 中     | 无运维手册，故障排查依赖个人经验                                       |
| `docs/rules/` 代码规范    | 中     | 无代码风格、重构规则、领域设计规范                                     |
| `docs/testing.md`         | 低     | 无测试策略文档                                                         |
| `docs/troubleshooting.md` | 低     | 无常见问题排查                                                         |

**改进建议**：优先补充 `CONTRIBUTING.md`、`CHANGELOG.md`、`SECURITY.md`，参考 yuiju 的 `docs/llm-contract.md` 创建 LLM 协定文档。

#### 4.1.3 `development-guide.md` 与代码不一致

**问题**：`development-guide.md` 第 64-80 行描述的后端代码结构包含 `src/agents/`、`src/tools/base.py`、`src/tools/registry.py`、`src/core/action_system.py`、`src/core/actions/`，但实际代码中：

- 不存在 `src/agents/` 目录（角色实现实际在 `src/core/character_tick.py`）
- 不存在 `src/tools/` 目录（MCP 集成在 `src/mcp/`）
- 不存在 `src/core/action_system.py`（Action 系统在 `src/actions/`）
- `src/core/actions/` 不存在（实际是 `src/actions/`）

**改进建议**：同步文档与代码结构，或考虑调整代码结构以匹配文档。

### 4.2 API 文档

#### 4.2.1 OpenAPI 自动生成可用但未充分利用

**现状**：FastAPI 自动生成 OpenAPI schema（`/docs`、`/redoc`），但：

- 端点缺少 `response_model` 定义，响应结构无法在文档中体现
- 端点缺少 `tags` 分类，40+ 端点在 Swagger UI 中平铺，难以浏览
- 错误响应未定义（`HTTPException` 的 status_code 与 detail 没有在文档中体现）
- `api-spec.md` 是手写文档，与自动生成的 OpenAPI 可能不同步

**改进建议**：

- 为每个端点添加 `response_model`、`tags`、`responses` 参数
- 按 `tags=["characters"]`、`tags=["world"]`、`tags=["messages"]` 等分类
- 用 `pnpm gen:api` 生成的类型与后端 OpenAPI 强制同步（CI 检查）

### 4.3 架构决策记录（ADR）

**问题**：项目做了多个重要技术选型（LangGraph、PostgreSQL + pgvector、Redis 作为实时状态真相源、MCP 协议、structlog + OTel + Langfuse 可观测性栈），但没有 ADR 记录决策背景与权衡。

**改进建议**：创建 `docs/adr/` 目录，按 `NNNN-title.md` 格式记录关键决策，至少补齐：

- `0001-langgraph-as-agent-framework.md`
- `0002-postgresql-pgvector-as-primary-db.md`
- `0003-redis-as-realtime-state-source.md`
- `0004-mcp-as-tool-protocol.md`
- `0005-structlog-otel-langfuse-observability-stack.md`

### 4.4 CHANGELOG

**问题**：无 `CHANGELOG.md`，`pyproject.toml` 中 `version = "0.1.0"` 长期不变，无法追踪版本迭代。

**改进建议**：采用 Keep a Changelog 格式，配合语义化版本号，每次发布记录变更。

---

## 五、DevOps 不足

### 5.1 CI/CD 配置

#### 5.1.1 ✅ CI/CD 已创建

**已完成**：`.github/workflows/ci.yml` 已创建，在 push/PR 到 main/master 时自动执行：

- **backend job**：启动 PG (pgvector/pgvector:pg18) + Redis 服务容器 → `uv sync` → `ruff check` → `ruff format --check` → `pytest`
- **frontend job**：`pnpm install` → `oxlint` → `tsc --noEmit` → `pnpm build`
- **mcp-servers job**：矩阵构建 4 个 MCP Server 的导入检查

**后续可增强**：

- 加入 `mypy` 检查步骤（当前 CI 不含 mypy，因项目存在大量预存类型标注缺失）
- 加入 `pytest --cov` 覆盖率门槛
- 加入 CD（自动部署）流水线

### 5.2 Docker 化程度

#### 5.2.1 后端 Dockerfile 已是多阶段构建但可优化

**现状**：`packages/backend/Dockerfile` 已使用多阶段构建（builder + 运行阶段），切换非 root 用户，配置 HEALTHCHECK。

**不足**：

- 未指定 `PYTHONHASHSEED=random`（虽然 Python 3.13 默认随机，但显式声明更安全）
- HEALTHCHECK 用 `python -c "import httpx; httpx.get(...)"` 启动 Python 解释器开销大，应改用 `curl` 或 `wget`
- 未使用 `.dockerignore`（实际有 `.dockerignore` 文件，但需确认覆盖范围）
- 未构建多架构镜像（`linux/amd64` + `linux/arm64`）
- 未推送至镜像仓库

#### 5.2.2 前端 Dockerfile 未审查但存在

**现状**：`packages/frontend/Dockerfile` 存在，配合 `nginx.conf` 提供静态资源服务。

#### 5.2.3 MCP Servers 单 Dockerfile 多构建

**现状**：`packages/mcp-servers/Dockerfile` 通过 `ARG SERVER` 支持构建不同 Server，`docker-compose.yml` 用 `args: SERVER: xxx` 区分。设计合理。

**改进建议**：

- 后端 HEALTHCHECK 改用 `curl -f http://localhost:8000/health || exit 1`
- 补充 `.dockerignore` 确保 `data/logs/`、`.venv/`、`__pycache__/` 不进入镜像
- 考虑多架构构建（`docker buildx`）

### 5.3 环境管理

#### 5.3.1 `.env.example` 不完整（见 2.4.2）

#### 5.3.2 缺少多环境配置

**问题**：项目仅有一个 `.env.example`，没有 `dev`/`staging`/`prod` 分环境配置。

**现状**：

- `docker-compose.yml`（生产）
- `docker-compose.infra.yml`（基础设施）
- `docker-compose-win.infra.yml`（Windows 基础设施）

**改进建议**：

- 创建 `.env.dev.example`、`.env.prod.example` 模板
- 或采用 `docker-compose.override.yml` 机制管理开发环境差异
- 敏感配置（`JWT_SECRET`、`OPENAI_API_KEY`、`MINIO_SECRET_KEY`）使用 Docker Secrets 或外部密钥管理（如 Vault）

### 5.4 监控告警

#### 5.4.1 可观测性栈完善但缺少告警规则

**现状**：`docker/observability/` 包含完整的可观测性栈：

- Prometheus（指标采集）
- Grafana（可视化，3 个预置 Dashboard）
- Loki + Alloy（日志采集）
- Jaeger（链路追踪）

**不足**：

- `docker/observability/prometheus.yml` 仅配置采集目标，没有告警规则（`alert_rules.yml`）
- Grafana 无告警通道配置（无 Alertmanager 集成、无邮件/钉钉/飞书 webhook）
- 无 runbook 文档说明告警处理流程
- `docker/observability/grafana/dashboards/` 有 3 个 Dashboard 但无告警面板

**改进建议**：

- 创建 `docker/observability/alert_rules.yml`，定义关键告警：
  - `redis_connected == 0`（Redis 断连）
  - `ai_town_world_tick_errors_total` 增长率 > 0.1/s（World Tick 错误）
  - `ai_town_llm_cost_total_usd` 日累计 > 预算 80%（成本告警）
  - `ai_town_active_characters == 0` 持续 5 分钟（无活跃角色）
  - HTTP 5xx 错误率 > 1%
- 在 Grafana 配置告警通道（飞书 webhook 适合国内团队）
- 创建 `docs/runbook.md` 记录常见告警的处理流程

---

## 六、安全不足

### 6.1 认证授权

#### 6.1.1 JWT 实现基本安全但缺少关键特性

**现状**：`src/auth/jwt_handler.py` 使用 `python-jose` 实现 JWT：

- HS256 对称签名
- 24 小时过期
- `sub` + `iat` + `exp` 标准 claims

**问题**：

- **HS256 对称密钥**：生产环境应使用 RS256/ES256 非对称签名，避免密钥泄露风险
- **无 token 刷新机制**：24 小时过期后用户必须重新登录，无 `refresh_token`
- **无 token 撤销机制**：token 一旦签发无法主动失效（如用户登出、密码修改后旧 token 仍有效）
- **无 jti claim**：无法唯一标识 token，无法实现黑名单
- **模块级单例问题**（见 2.3.2）：`_handler` 在导入时实例化，运行时修改 `jwt_secret` 不生效

**改进建议**：

- 短期：补充 `jti` claim，实现 Redis 黑名单（登出/密码修改时加入 jti）
- 中期：引入 `refresh_token` 机制，access_token 短期（1 小时）+ refresh_token 长期（7 天）
- 长期：迁移到 RS256，密钥对由 Vault 或 KMS 管理

#### 6.1.2 无 RBAC，仅单一管理员角色

**现状**：`AuthMiddleware`（`main.py` 第 454-530 行）仅区分"已认证"与"未认证"，所有已认证用户拥有相同权限。

**问题**：

- `ADMIN_USERNAME` / `ADMIN_PASSWORD` 是唯一管理员账号，无法支持多用户多角色场景
- 公开 GET 端点（`PUBLIC_GET_PREFIXES`）硬编码在中间件中，新增公开端点必须改中间件代码
- 无角色字段，无法区分"观察者"（只读）、"操作者"（可触发 Tick）、"管理员"（可改配置）

**改进建议**：

- 引入角色字段（`role: admin | operator | viewer`）写入 JWT claims
- 改用装饰器或依赖注入标注端点所需角色：`@require_role("admin")`
- 公开端点配置改为从配置文件或数据库读取，而非硬编码

#### 6.1.3 默认管理员密码不安全

**问题**：`config.py` 第 56-57 行：

```python
admin_username: str = "admin"
admin_password: str = "admin123"
```

`.env.example` 也使用 `ADMIN_PASSWORD=admin123`。如果部署时未修改，攻击者可直接登录。

**改进建议**：

- 启动时检查 `admin_password == "admin123"`，若是则拒绝启动并提示修改
- 或首次启动强制设置密码

### 6.2 输入验证

#### 6.2.1 Prompt 注入防护已有但覆盖不全

**现状**：`src/security/prompt_guard.py` 实现了三层防护：

- 检测（`check_injection`）：16 个危险模式（角色覆盖、系统提示泄露、权限提升、代码执行、数据泄露）
- 消毒（`sanitize_user_input`）：移除控制字符 + 注入模式 + HTML 转义 + 长度限制
- 包装（`wrap_user_message` / `build_safe_prompt`）：分隔符隔离 + 反注入指令

**问题**：

- **覆盖范围有限**：`PromptGuard` 仅在 `MessageService` 中使用，`CharacterTickEngine` 的决策 prompt、`ReflectionService` 的反思 prompt 是否使用未确认
- **正则模式可被绕过**：如 `ign0re instructions`、Unicode 变体、base64 编码等可绕过正则匹配
- **无 LLM 二次校验**：仅靠正则，未用轻量 LLM（如 `model_flash`）做语义级注入检测
- **`build_safe_prompt` 未实际使用**：搜索代码发现 `MessageService` 可能仅用 `sanitize_user_input`，未用完整的 `build_safe_prompt`

**改进建议**：

- 审计所有 LLM 调用路径，确保用户输入都经过 `PromptGuard`
- 对高风险场景（群聊消息、API 开放接口）增加 LLM 语义级注入检测
- 补充 Unicode 正则化（NFKC）防止变体绕过

#### 6.2.2 SQL 注入防护

**现状**：使用 SQLAlchemy ORM + 参数化查询，`main.py` 中的原生 SQL（如 `pre_create_partitions`）使用 `text()` + 参数化。

**问题**：

- `get_proactive_shares` 端点（第 2433 行）使用 `text("extra_data->>'share_type' = 'proactive'")` 拼接 SQL，虽然此处是常量字符串无注入风险，但模式不规范
- `get_recent_logs` 端点读取文件系统日志，`lines` 参数有 `min(max(lines, 1), 500)` 边界检查，但 `level` 参数未校验枚举值

**改进建议**：

- 为 `level` 参数添加 `Literal["debug", "info", "warning", "error"]` 类型标注
- 审计所有 `text()` 用法，确保无字符串拼接

### 6.3 密钥管理

#### 6.3.1 无密钥轮换机制

**问题**：`jwt_secret`、`openai_api_key`、`minio_secret_key`、`langfuse_secret_key` 等密钥一旦配置无法热更新，轮换需要重启服务。

**改进建议**：

- 关键密钥支持从 Vault/AWS Secrets Manager 动态读取
- JWT 密钥支持多密钥共存（old + new），实现平滑轮换

#### 6.3.2 密钥可能出现在日志中

**问题**：`main.py` 第 127 行 `logger.info("redis_connected", url=settings.redis_url)`，`redis_url` 可能包含密码（`redis://:password@host:port`）。`llm_initialized` 日志未记录 API Key，但其他日志可能间接泄露。

**改进建议**：

- 日志中对 URL 进行脱敏（移除密码部分）
- 引入 structlog processor 自动脱敏敏感字段（`password`、`api_key`、`secret`、`token`）

### 6.4 速率限制

#### 6.4.1 RateLimiter 已实现但未充分使用

**现状**：`src/security/rate_limiter.py` 实现了基于 Redis 的固定窗口限流：

- `check(key, max_requests, window_seconds)`：检查并自增
- `get_remaining`：查询剩余配额
- `reset`：重置计数器

**问题**：

- **固定窗口算法**：存在边界突刺问题（窗口切换瞬间可能通过 2 倍流量），应改用滑动窗口或令牌桶
- **未在 API 端点使用**：搜索代码发现 `rate_limiter` 全局变量在 `main.py` 初始化，但 40+ 个 API 端点中几乎没有调用 `rate_limiter.check()`
- **未覆盖消息发送**：`/api/v1/messages/send` 是最容易被滥用的端点（每次调用消耗 LLM token），但没有限流
- **未覆盖登录接口**：`/api/v1/auth/login` 无限流，可被暴力破解
- **未区分用户/IP**：现有 `check(key)` 需要调用方传入 key，没有统一的"按用户/IP 自动限流"装饰器

**改进建议**：

- 实现 FastAPI 限流依赖：`@app.post("/api/v1/messages/send", dependencies=[Depends(rate_limit(key="user:{user_id}", limit=60, window=60))])`
- 登录接口强制限流（如 5 次/分钟/IP）
- 消息发送按用户限流（如 60 条/分钟）
- 算法升级为滑动窗口（Redis ZSET 实现）或令牌桶

---

## 七、与 yuiju 项目对比分析与学习要点

### 7.1 yuiju 项目亮点

#### 7.1.1 ✅ AGENTS.md 规范文件已创建

**yuiju 做法**：根目录 `AGENTS.md` 定义了 AI Coding 执行协议。

**aitown 现状**：**已完成**。根目录 `AGENTS.md` 已创建，包含：

- 代码风格入口（指向 `docs/rules/implementation-style.md` 等 4 个规范文档）
- AI Coding 执行协议（写代码前必须说明技术方案、需求不明确必须询问、项目规范优先于 AI 通用习惯、不新增防御性逻辑/兜底/fallback）
- 项目约束（uv + pnpm monorepo、各包职责、Prompt 集中维护位置、配置真相源）
- 架构约定（Redis 是实时状态真相源、PG 保存历史、Action 必须有 precondition、LLM 边界）
- 验证命令（`uv run ruff check` + `uv run mypy` + `uv run pytest` + `pnpm run lint` + `pnpm run typecheck`）

#### 7.1.2 ✅ 分层文档规范体系已创建

**yuiju 做法**：`docs/rules/` 目录下 4 套规范。

**aitown 现状**：**已完成**。`docs/rules/` 目录已创建，包含 4 套规范文档：

- `implementation-style.md`：代码风格（6 大核心原则、7 种常见坏代码形态、自查清单）
- `domain-design-style.md`：领域设计规范（领域语言、限界上下文、分层落点、真相源与副作用）
- `prompt-style.md`：Prompt 规范（视角、人设与任务分离、工程概念不外泄、维护位置集中）
- `refactor-style.md`：重构规则

被 `AGENTS.md` 引用为代码风格入口，AI 辅助开发时强制遵循。

#### 7.1.3 LLM 协定文档

**yuiju 做法**：`docs/llm-contract.md` 明确定义：

- 总原则：LLM 是决策和文本生成能力，不是状态真相源
- LLM 可以做什么：基于上下文选择 Action、生成消息、整理日记、总结记忆
- LLM 不能做什么：不直接修改 Redis/MongoDB/文件/外部平台状态
- Prompt 维护位置：集中维护在 `@yuiju/utils/src/prompt/`
- World 决策边界：5 步主流程（状态读取 → precondition 过滤 → LLM 选择 → executor 执行 → 后续业务流程处理）
- Message 生成边界：不暴露 Action/schema/字段名等工程概念
- Memory 与 Diary 边界：Episode 是事实记录，Diary 是叙事归档
- 模型来源：`yuiju.config.ts` 的 `llm.models` 配置，每类可多源备用
- 禁止事项：5 条明确禁止

**aitown 现状**：`docs/architecture.md` 的"设计原则"部分有类似内容（状态驱动、事实优先、闭环演化、模块解耦），但分散在架构文档中，没有独立的 LLM 协定文档，Prompt 维护位置不明确（`src/llm/prompts.py` 与 `configs/prompts/*.yaml` 并存）。

#### 7.1.4 单一配置源

**yuiju 做法**：根目录 `yuiju.config.ts` 统一管理所有业务配置：

- `app`（应用设置：时区、内存目录）
- `database`（MongoDB + Redis URL）
- `llm.models`（chat/strong/flash/vision 四类，每类多源）
- `llm.hermesAgent`（专用 agent 模型）
- `message.internalApi`（内部 API）
- `message.proactive`（主动推送目标）
- `message.onebot`（QQ 配置：协议、selfId、endpoint、token、重试策略、白名单）
- `message.lark`（飞书配置）
- `message.stickers`（表情包配置：每个表情有 uri + description）

配置文件是 TypeScript，有类型校验（`defineYuijuConfig` 函数 + `config-schema.ts`），`.env` 仅保留 `NODE_ENV`。

**aitown 现状**：`.env` + `config.py`（90+ 字段）+ Redis 运行时覆盖（12 个键），三份配置来源，真相源不明确（见 2.4.1）。

#### 7.1.5 PM2 进程管理

**yuiju 做法**：`ecosystem.config.js` 使用 PM2 管理三个进程：

- `yuiju-message`（消息处理）
- `yuiju-world`（世界引擎）
- `yuiju-web`（Web 界面）

每个进程配置 `max_memory_restart: "1024M"`，`autorestart: false`（手动控制重启策略）。

**aitown 现状**：使用 Docker Compose 编排，每个服务一个容器，`restart: unless-stopped`。Docker 方案更适合云原生部署，但 PM2 的进程级管理在单机部署时更轻量。aitown 可考虑补充 PM2 方案作为"单机快速部署"选项。

#### 7.1.6 Biome 统一工具链

**yuiju 做法**：`biome.json` 统一 format + lint：

- formatter：space 缩进、2 字符、100 列宽
- linter：recommended 规则集 + 自定义 suspicious/style/correctness/a11y 规则
- css：支持 Tailwind directives
- files：精确的 ignore 列表

单一工具替代 ESLint + Prettier + stylelint，配置简单、性能高。

**aitown 现状**：前端用 `oxlint.json`，后端用 `ruff`，工具链分散但各自合理。aitown 的工具选择实际上更现代（oxlint 比 biome 更快，ruff 是 Python 生态标杆），但缺少强制执行（无 pre-commit）。

#### 7.1.7 Husky pre-commit

**yuiju 做法**：`.husky/pre-commit` 执行：

```bash
pnpm run lint-staged
pnpm run type-check
```

确保提交前必须通过 lint 和类型检查。

**aitown 现状**：无任何 git hooks，开发者可提交未通过检查的代码。

#### 7.1.8 CI/CD 流水线

**yuiju 做法**：`.github/workflows/ci.yml` 在 push/PR 时自动执行 lint + type-check + build。还有 `pr-merged.yml` 处理合并后流程。

**aitown 现状**：完全无 CI/CD（见 5.1.1）。

#### 7.1.9 记忆系统设计

**yuiju 做法**：`packages/utils/src/memory/person-memory/` 分 6 个文件：

- `directory.ts`：目录管理（`listPersonMemories`）
- `format.ts`：格式化
- `heat.ts`：热度管理（`initializePersonMemoryHeat`）
- `storage.ts`：存储（`getPersonMemory`）
- `types.ts`：类型定义
- `update.ts`：更新（`applyPersonMemoryProposalToDocument`、`updatePersonMemory`）

每个文件职责单一，模块边界清晰。还有 `diary.ts` 定义日记模型（`MemoryDiaryEntry`），明确"Diary 是基于 Episode 生成的叙事归档，不替代 Episode 真相源"。

**aitown 现状**：`src/memory/` 包含 `episode_service.py`、`reflection_service.py`、`retrieval_service.py`、`embedding_worker.py`，但缺少：

- 无 person-memory 概念（角色对用户的记忆）
- 无 diary 系统（角色日记）
- 无记忆热度（heat）管理
- 记忆类型分层不够细

#### 7.1.10 Prompt 集中管理

**yuiju 做法**：`@yuiju/utils/src/prompt/` 集中维护所有 Prompt：

- `character-card.ts`（角色卡）
- `diary.ts`（日记）
- `everything.ts`（综合）
- `group-memory.ts`（群聊记忆）
- `index.ts`（导出）
- `message.ts`（消息）
- `person-memory.ts`（个人记忆）
- `phone.ts`（手机）
- `plan-review.ts`（计划评审）
- `proactive-message.ts`（主动消息）
- `utils.ts`（工具）
- `world-guide.ts`（世界指南）
- `world-map.ts`（世界地图）
- `world-view.ts`（世界观）

无参数静态 Prompt 优先导出常量，业务包只组合上下文。

**aitown 现状**：Prompt 分散在两处：

- `src/llm/prompts.py`（Python 代码）
- `configs/prompts/*.yaml`（YAML 文件：chat.yaml、decision.yaml、reflection.yaml）

两处 Prompt 的关系不明确，`PromptTemplates` 类如何加载 YAML 不透明。

#### 7.1.11 LLM 工具系统

**yuiju 做法**：`packages/utils/src/llm/tools/` 有标准化的工具封装：

- `query-state.ts`（查询状态）
- `memory-search.ts`（记忆检索）
- `person-memory.ts`（个人记忆操作）
- `propose-plan-changes.ts`（提议计划变更）
- `review-plan-changes.ts`（评审计划变更）
- `query-available-inventory-items.ts`（查询库存）
- `query-static-guide.ts`（查询静态指南）
- `schema/`（工具 schema 定义）

每个工具有独立的 schema 定义和实现，标准化封装。

**aitown 现状**：MCP 工具分散在 `packages/mcp-servers/` 各 Server 中，缺少统一的工具 schema 定义层。`src/mcp/client.py` 是 MCP 客户端，但没有标准化的工具封装模式。

#### 7.1.12 日记系统

**yuiju 做法**：角色可以根据经历整理日记：

- `packages/utils/src/memory/diary.ts`：定义 `MemoryDiaryEntry`（subject、period、diaryDate、diaryEndDate、text）
- `packages/world/src/memory/diary/`：日记生成逻辑（`day.ts`、`summary.ts`）
- 支持 day/week/month/year 四种周期

让"记得今天"变成具体内容，角色可以回顾"我今天做了什么"。

**aitown 现状**：无日记系统，记忆只有 `memory_episodes`（事件记录）和 `reflections`（反思），缺少叙事归档层。

#### 7.1.13 多模型备用源

**yuiju 做法**：`packages/utils/src/llm/models.ts` 实现多源备用：

- 每类模型（chat/strong/flash/vision）可配置多个 OpenAI-compatible source
- `LlmModelSourceAvailability` 类管理冷却（失败后冷却 5 分钟）
- `createFallbackModel` 函数包装多源，按顺序尝试，失败自动切换
- 冷却中的源排到候选列表末尾，仍可作为最后兜底

```typescript
const MODEL_SOURCE_FAILURE_COOLDOWN_MS = 5 * 60 * 1000;
```

**aitown 现状**：`src/llm/client.py` 仅支持单一 OpenAI endpoint（`settings.openai_api_key` + `settings.openai_base_url`），无备用源切换机制。LLM 调用失败时依靠 `circuit_breaker` 熔断，但无法自动切换到备用模型。

#### 7.1.14 VitePress 文档站

**yuiju 做法**：`apps/site/` 使用 VitePress 构建文档网站：

- `.vitepress/config.mts`：站点配置
- `.vitepress/theme/`：自定义主题
- `index.md`：首页
- 配套 `docs/introduction/tech-introduction/` 含 SVG 流程图（character-state-flow、memory-flow、message-flow、world-state-flow）

**aitown 现状**：17 个 Markdown 文档散落在 `docs/` 目录，无文档站，阅读体验依赖 GitHub/IDE 渲染。

#### 7.1.15 Docker 单镜像一键部署

**yuiju 做法**：`docs/docker-one-click.md` 实现单镜像一键部署，适合个人用户快速试用。

**aitown 现状**：`docker-compose.yml` 编排 10+ 服务（postgres/redis/minio/backend/frontend/mcp-servers/observability），虽然功能完整但部署门槛高。无"单镜像快速试用"方案。

### 7.2 对比差距矩阵（2026-07-13 更新）

| 维度             | aitown 现状                                             | yuiju 现状                     | 差距          | 改进优先级 | 状态 |
| ---------------- | ------------------------------------------------------- | ------------------------------ | ------------- | ---------- | ---- |
| AI Coding 规范   | ✅ 有 `AGENTS.md`                                       | 有 `AGENTS.md` + 执行协议      | 已消除        | P0         | ✅   |
| 代码风格规范     | ✅ 有 `docs/rules/` 4 套规范                            | 有 4 套规范文档                | 已消除        | P0         | ✅   |
| LLM 协定文档     | 散落在架构文档                                          | 独立 `llm-contract.md`         | 中            | P1         | ❌   |
| 配置管理         | `.env` + `config.py` + Redis 覆盖（三源）               | `yuiju.config.ts` 单一源       | 高            | P1         | ❌   |
| CI/CD            | ✅ 有 GitHub Actions                                    | GitHub Actions                 | 已消除        | P0         | ✅   |
| Pre-commit hooks | 无                                                      | Husky + lint-staged            | 高            | P0         | ❌   |
| 类型检查强制     | mypy 配置存在但未强制                                   | CI 中强制 type-check           | 中            | P1         | ❌   |
| 测试覆盖         | 251 个测试通过，核心模块无覆盖                          | （未审查）                     | 高            | P1         | ❌   |
| 全局变量依赖     | ✅ `src/runtime.py` 依赖容器                            | 依赖注入                       | 已消除        | P1         | ✅   |
| 路由组织         | ✅ 541 行 + 11 个路由模块                               | 分包路由                       | 已消除        | P1         | ✅   |
| 记忆系统         | Episode + Reflection                                    | + Person Memory + Diary + Heat | 中            | P2         | ❌   |
| Prompt 管理      | ✅ YAML 外置 + 人设分层                                 | 集中在 `utils/src/prompt/`     | 已消除        | P1         | ✅   |
| LLM 工具系统     | MCP Server 分散                                         | 标准化工具封装                 | 中            | P2         | ❌   |
| 多模型备用源     | 单一 endpoint                                           | 多源 + 冷却切换                | 高            | P1         | ❌   |
| 日记系统         | 无                                                      | 有（day/week/month/year）      | 低            | P2         | ❌   |
| 文档站           | 无                                                      | VitePress                      | 低            | P2         | ❌   |
| 进程管理         | Docker Compose                                          | PM2 + Docker                   | 低            | P2         | —    |
| 一键部署         | 多容器编排                                              | 单镜像一键                     | 低            | P2         | —    |
| 统一工具链       | ruff + oxlint（分散但合理）                             | Biome（统一）                  | 低            | P2         | —    |
| Prompt 深度      | ✅ 人设分层/状态泄露防护/跨世界边界/真实感三原则        | 极细腻                         | 已消除        | P0         | ✅   |
| 多智能体交互     | ✅ chat_with Action + 场景感知 + 双记忆持久化           | 单角色无此需求                 | aitown 已超越 | —          | ✅   |
| 可观测性         | 完整（Prometheus + Grafana + Loki + Jaeger + Langfuse） | （未审查）                     | aitown 优势   | —          | —    |
| 数据库           | PostgreSQL + pgvector（向量检索 + JSONB + 分区表）      | MongoDB                        | aitown 优势   | —          | —    |
| 成本控制         | 预算管理 + 熔断器                                       | （未审查）                     | aitown 优势   | —          | —    |
| Prompt 防护      | PromptGuard 三层防护                                    | （未审查）                     | aitown 优势   | —          | —    |
| 多模态           | 图像生成 + 视频生成                                     | （未审查）                     | aitown 优势   | —          | —    |

### 7.3 可直接借鉴的具体实践

#### 实践 1：创建 AGENTS.md

- **借鉴内容**：AI Coding 执行协议与项目硬约束
- **yuiju 做法**：`AGENTS.md` 定义写代码前必须说明技术方案、需求不明确必须询问、不新增防御性逻辑/兜底、项目规范优先于 AI 通用习惯、验证命令
- **aitown 改造**：在项目根目录创建 `AGENTS.md`，内容包含：
  - 代码风格入口（指向 `docs/rules/implementation-style.md`）
  - AI Coding 执行协议（适配 Python 生态）
  - 项目约束（uv + pnpm monorepo、各包职责、Prompt 维护位置、配置真相源）
  - 架构约定（Redis 是实时状态真相源、PG 保存历史、Action 必须有 precondition）
  - 验证命令（`uv run ruff check` + `uv run mypy src/` + `uv run pytest` + `pnpm run lint`）
- **预期收益**：AI 辅助开发时遵循项目规范，减少代码风格不一致

#### 实践 2：创建 `docs/rules/` 规范体系

- **借鉴内容**：分层代码规范文档
- **yuiju 做法**：4 套规范（implementation-style、domain-design-style、prompt-style、refactor-style），每套有核心原则 + 常见坏代码形态 + 自查清单
- **aitown 改造**：创建 `docs/rules/` 目录，针对 Python 生态和 aitown 领域模型编写：
  - `implementation-style.md`：适配 Python（类型标注、async/await、Pydantic 使用）
  - `domain-design-style.md`：基于 aitown 领域语言（Character/World/Scene/Action/Tick/Plan/MemoryEpisode/Reflection/Message）
  - `prompt-style.md`：Prompt 维护位置（统一到 `configs/prompts/` 还是 `src/llm/prompts.py`）
  - `refactor-style.md`：重构规则
- **预期收益**：代码风格统一，新成员快速上手，重构有据可依

#### 实践 3：创建 LLM 协定文档

- **借鉴内容**：明确 LLM 能做什么、不能做什么
- **yuiju 做法**：`docs/llm-contract.md` 定义总原则、Prompt 维护位置、World 决策边界、Message 生成边界、Memory 边界、禁止事项
- **aitown 改造**：创建 `docs/llm-contract.md`，内容包含：
  - 总原则：LLM 是决策和生成能力，不是状态真相源
  - Prompt 维护位置：明确 `configs/prompts/*.yaml` 与 `src/llm/prompts.py` 的关系
  - World 决策边界：Character Tick 五阶段中 LLM 的介入点
  - Message 生成边界：不暴露 Action/schema/字段名
  - Memory 边界：Episode 是事实记录，Reflection 是高层认知
  - 多模态边界：图像/视频生成的调用约束
- **预期收益**：LLM 调用边界清晰，避免 Prompt 散写、状态被 LLM 直接修改等问题

#### 实践 4：引入 Pre-commit hooks

- **借鉴内容**：提交前自动检查
- **yuiju 做法**：`.husky/pre-commit` 执行 `lint-staged` + `type-check`
- **aitown 改造**：使用 Python 生态的 `pre-commit` 框架，创建 `.pre-commit-config.yaml`：
  ```yaml
  repos:
    - repo: https://github.com/astral-sh/ruff-pre-commit
      rev: v0.8.0
      hooks:
        - id: ruff
          args: [--fix]
        - id: ruff-format
    - repo: https://github.com/pre-commit/mirrors-mypy
      rev: v1.14.0
      hooks:
        - id: mypy
          files: ^packages/backend/src/
    - repo: local
      hooks:
        - id: pytest
          name: pytest
          entry: uv run pytest
          language: system
          types: [python]
          pass_filenames: false
  ```
- **预期收益**：提交前自动修复 lint 问题、检查类型、运行测试，避免低质量代码进入仓库

#### 实践 5：创建 CI/CD 流水线

- **借鉴内容**：自动化测试与构建
- **yuiju 做法**：`.github/workflows/ci.yml` 执行 lint + type-check + build
- **aitown 改造**：创建 `.github/workflows/ci.yml`，包含后端（ruff + mypy + pytest）+ 前端（oxlint + tsc + build）+ MCP Servers 基础检查。可参考本文档 5.1.1 的具体配置。
- **预期收益**：PR 合并前自动验证，减少回归问题

#### 实践 6：实现多模型备用源

- **借鉴内容**：LLM 多源备用 + 冷却机制
- **yuiju 做法**：`packages/utils/src/llm/models.ts` 的 `createFallbackModel` 函数，每类模型配置多个 source，失败自动切换 + 5 分钟冷却
- **aitown 改造**：重构 `src/llm/client.py` 的 `LLMClient`：
  - `config.py` 的 `model_chat`/`model_strong`/`model_flash` 改为列表类型，每项含 `base_url` + `api_key` + `model`
  - 或新增 `configs/llm-models.yaml` 配置多源
  - `LLMClient` 内部维护 `LlmSourceAvailability` 类，管理冷却
  - `chat()` / `structured_output()` 方法按顺序尝试，失败切换
- **预期收益**：LLM 服务商故障时自动切换，提高可用性；可配置低成本备用模型

#### 实践 7：Prompt 集中管理

- **借鉴内容**：Prompt 统一维护位置
- **yuiju 做法**：`@yuiju/utils/src/prompt/` 集中维护所有 Prompt，无参数静态 Prompt 导出常量
- **aitown 改造**：明确 Prompt 真相源：
  - 方案 A（推荐）：统一到 `configs/prompts/*.yaml`，`PromptTemplates` 类负责加载，`src/llm/prompts.py` 仅做加载逻辑
  - 方案 B：统一到 `src/llm/prompts.py`，移除 `configs/prompts/` 目录
  - 无论哪种方案，确保 Prompt 修改有单一位置
- **预期收益**：Prompt 修改有据可依，避免双处维护导致不一致

#### 实践 8：引入日记系统

- **借鉴内容**：角色日记作为叙事归档层
- **yuiju 做法**：`MemoryDiaryEntry` 模型 + `packages/world/src/memory/diary/` 生成逻辑，支持 day/week/month/year 周期
- **aitown 改造**：
  - 新增 `character_diaries` 表（character_id、period、diary_date、text、generated_at）
  - 新增 `src/memory/diary_service.py`，在 Character Tick 中定期触发日记生成
  - 日记基于 `memory_episodes` 生成，不替代 Episode 真相源
  - 前端新增日记查看页面
- **预期收益**：角色有"今天做了什么"的具体叙事，提升陪伴感；记忆系统分层更完整

#### 实践 9：Person Memory（角色对用户的记忆）

- **借鉴内容**：角色对每个用户的独立记忆
- **yuiju 做法**：`packages/utils/src/memory/person-memory/` 分 6 个文件管理（directory/format/heat/storage/types/update）
- **aitown 改造**：
  - 新增 `person_memories` 表（character_id、user_id、content、heat、updated_at）
  - 新增 `src/memory/person_memory_service.py`
  - 消息处理时更新角色对用户的记忆（用户偏好、关系进展、共同话题）
  - LLM 上下文构造时检索相关 person memory
- **预期收益**：角色对每个用户有独立记忆，陪伴关系更个性化

#### 实践 10：VitePress 文档站

- **借鉴内容**：文档网站
- **yuiju 做法**：`apps/site/` 使用 VitePress，含自定义主题、SVG 流程图
- **aitown 改造**：在 `packages/docs-site/` 创建 VitePress 站点：
  - 将 `docs/` 的 17 个 Markdown 导入为 VitePress 页面
  - 添加导航栏与侧边栏
  - 绘制架构流程图（mermaid 或 SVG）
  - 部署到 GitHub Pages 或 Vercel
- **预期收益**：文档阅读体验提升，适合对外展示

---

## 八、改进路线图

### 8.1 P0（紧急，1-2 周内）

#### 8.1.1 创建 CI/CD 流水线

- **目标**：PR 合并前自动执行 lint + type-check + test
- **步骤**：
  1. 创建 `.github/workflows/ci.yml`
  2. 后端 job：`uv sync` → `ruff check` → `ruff format --check` → `mypy src/` → `pytest`
  3. 前端 job：`pnpm install` → `oxlint` → `tsc --noEmit` → `pnpm build`
  4. 在 PR 模板中要求 CI 通过才能合并
- **验收**：PR 时 CI 自动运行，失败阻止合并

#### 8.1.2 引入 Pre-commit hooks

- **目标**：提交前自动检查
- **步骤**：
  1. 创建 `.pre-commit-config.yaml`（ruff + mypy）
  2. 后端开发者执行 `pre-commit install`
  3. 前端可考虑用 `husky` + `lint-staged`（或简单方案：`pre-commit` 框架的本地 hook）
- **验收**：`git commit` 时自动触发检查，失败阻止提交

#### 8.1.3 创建 AGENTS.md

- **目标**：AI Coding 执行协议
- **步骤**：
  1. 参考本文件 7.3 实践 1
  2. 在项目根目录创建 `AGENTS.md`
  3. 内容包含代码风格入口、执行协议、项目约束、架构约定、验证命令
- **验收**：AI 辅助开发时遵循协议

#### 8.1.4 创建 CONTRIBUTING.md

- **目标**：贡献指南
- **步骤**：
  1. 创建 `CONTRIBUTING.md`
  2. 内容包含：开发环境搭建、代码规范入口、提交规范（Conventional Commits）、PR 流程、测试要求
- **验收**：外部贡献者可按指南提交 PR

#### 8.1.5 修复默认管理员密码安全问题

- **目标**：防止默认密码被利用
- **步骤**：
  1. `main.py` 的 `lifespan` 中检查 `settings.admin_password == "admin123"`，若是则拒绝启动并提示修改
  2. 或首次启动强制设置密码
  3. 更新 `.env.example` 注释强调"生产环境必须修改"
- **验收**：默认密码无法在生产环境使用

### 8.2 P1（重要，1 个月内）

#### 8.2.1 消除全局变量 + `from src.main import` 反模式

- **目标**：解除 6 个模块对 `main.py` 的反向依赖
- **步骤**：
  1. 创建 `src/runtime.py`，将 `redis`、`llm`、`prompts`、`ws_manager`、`onebot_adapter` 等全局变量迁移至此
  2. `main.py` 的 `lifespan` 初始化后写入 `src/runtime.py`
  3. `adapters/`、`messaging/`、`core/` 改为从 `src/runtime.py` 导入
  4. 或采用 FastAPI `Depends` 机制注入依赖
- **验收**：`grep -r "from src.main import" src/` 无结果

#### 8.2.2 拆分 `main.py` 路由

- **目标**：将 3111 行的 `main.py` 拆分为模块化路由
- **步骤**：
  1. 创建 `src/api/` 目录
  2. 按领域拆分：`characters.py`、`world.py`、`messages.py`、`mcp.py`、`admin.py`、`notifications.py`、`modules.py`
  3. 使用 `APIRouter` 聚合，`main.py` 仅保留 `app = FastAPI()` + `include_router`
  4. `_MCP_SERVERS_CONFIG` 迁移到 `src/mcp/registry.py`
  5. `AuthMiddleware` 迁移到 `src/auth/middleware.py`
- **验收**：`main.py` < 200 行，各路由模块独立可测

#### 8.2.3 实现多模型备用源

- **目标**：LLM 服务商故障时自动切换
- **步骤**：
  1. 修改 `config.py`，`model_chat`/`model_strong`/`model_flash` 改为支持多源配置
  2. 或新增 `configs/llm-models.yaml`
  3. 重构 `LLMClient`，内部维护源可用性 + 冷却
  4. `chat()` / `structured_output()` 按顺序尝试
- **验收**：模拟主源故障，自动切换到备用源

#### 8.2.4 创建 `docs/rules/` 规范体系

- **目标**：代码风格与领域设计规范
- **步骤**：
  1. 创建 `docs/rules/` 目录
  2. 编写 `implementation-style.md`（适配 Python）
  3. 编写 `domain-design-style.md`（基于 aitown 领域语言）
  4. 编写 `prompt-style.md`
  5. 编写 `refactor-style.md`
- **验收**：规范文档被 `AGENTS.md` 引用，开发者遵循

#### 8.2.5 创建 LLM 协定文档

- **目标**：明确 LLM 边界
- **步骤**：
  1. 创建 `docs/llm-contract.md`
  2. 内容参考本文件 7.3 实践 3
- **验收**：LLM 调用边界文档化

#### 8.2.6 补充核心模块测试

- **目标**：核心业务模块有测试覆盖
- **步骤**：
  1. 使用 `testcontainers` 启动 PG + Redis
  2. 为 `MessageService.handle_user_message` 补充集成测试
  3. 为 `WorldEngine` 补充单元测试
  4. 为 `CharacterTickEngine` 补充单元测试（mock LLM）
  5. 为关键 Repository 补充 CRUD 测试
  6. CI 中加入覆盖率门槛（初始 40%）
- **验收**：核心模块测试覆盖率 ≥ 40%

#### 8.2.7 配置管理优化

- **目标**：消除配置三源问题
- **步骤**：
  1. 将运行时配置项收敛到独立的 `RuntimeConfig` Pydantic 模型
  2. 从 Redis 加载并校验类型
  3. 业务代码通过 `runtime_config.get("character_tick_seconds")` 读取
  4. 同步 `.env.example` 与 `Settings` 字段
- **验收**：配置真相源明确，类型校验生效

#### 8.2.8 注册全局异常处理中间件

- **目标**：统一错误响应格式
- **步骤**：
  1. 注册 `@app.exception_handler(Exception)` 全局处理器
  2. 区分 `HTTPException`、`ValueError`、其他异常
  3. 响应格式：`{"detail": "...", "trace_id": "...", "error_code": "..."}`
  4. 内部异常不暴露 `str(e)`
- **验收**：所有 API 错误响应格式统一

### 8.3 P2（增强，2-3 个月）

#### 8.3.1 引入日记系统

- **目标**：角色有日记叙事归档
- **步骤**：
  1. 新增 `character_diaries` 表 + Alembic 迁移
  2. 新增 `src/memory/diary_service.py`
  3. Character Tick 中定期触发日记生成
  4. 前端新增日记查看页面
- **验收**：角色每天生成一篇日记，前端可查看

#### 8.3.2 引入 Person Memory

- **目标**：角色对每个用户有独立记忆
- **步骤**：
  1. 新增 `person_memories` 表
  2. 新增 `src/memory/person_memory_service.py`
  3. 消息处理时更新记忆
  4. LLM 上下文构造时检索
- **验收**：角色对不同用户有不同的记忆上下文

#### 8.3.3 RBAC 权限系统

- **目标**：多角色权限管理
- **步骤**：
  1. 引入角色字段（admin/operator/viewer）
  2. JWT claims 写入 role
  3. 端点用 `@require_role("admin")` 装饰器
  4. 公开端点配置外置
- **验收**：不同角色有不同权限

#### 8.3.4 JWT 安全增强

- **目标**：支持 token 刷新与撤销
- **步骤**：
  1. 引入 `refresh_token` 机制
  2. 补充 `jti` claim + Redis 黑名单
  3. 迁移到 RS256 非对称签名
- **验收**：token 可主动撤销，密钥可轮换

#### 8.3.5 速率限制覆盖关键端点

- **目标**：防止 API 滥用
- **步骤**：
  1. 实现滑动窗口限流（Redis ZSET）
  2. 实现 FastAPI 限流依赖
  3. 登录接口限流（5 次/分钟/IP）
  4. 消息发送限流（60 条/分钟/用户）
- **验收**：关键端点有限流保护

#### 8.3.6 创建 VitePress 文档站

- **目标**：文档网站
- **步骤**：
  1. 创建 `packages/docs-site/`
  2. 配置 VitePress
  3. 导入 `docs/` 的 Markdown
  4. 部署到 GitHub Pages
- **验收**：文档站可访问

#### 8.3.7 创建 ADR 体系

- **目标**：架构决策记录
- **步骤**：
  1. 创建 `docs/adr/` 目录
  2. 补齐关键决策（LangGraph、PG+pgvector、Redis 真相源、MCP、可观测性栈）
  3. 后续重要决策按 ADR 流程记录
- **验收**：关键决策有 ADR

#### 8.3.8 补充告警规则与 Runbook

- **目标**：监控告警与故障处理
- **步骤**：
  1. 创建 `docker/observability/alert_rules.yml`
  2. 定义关键告警（Redis 断连、World Tick 错误、成本超预算、无活跃角色、HTTP 5xx）
  3. Grafana 配置告警通道（飞书 webhook）
  4. 创建 `docs/runbook.md`
- **验收**：告警规则生效，有处理手册

#### 8.3.9 单镜像一键部署

- **目标**：降低试用门槛
- **步骤**：
  1. 创建 `docs/docker-one-click.md`
  2. 构建包含后端 + 前端 + 内嵌 SQLite/Redis 的单镜像
  3. 提供 `docker run` 一键启动命令
- **验收**：单命令启动试用环境

---

## 九、总结

### 9.1 aitown 的核心优势（应保持）

1. **可观测性栈完整**：structlog + OTel + Prometheus + Grafana + Loki + Jaeger + Langfuse，全链路追踪与指标体系完善，这是 yuiju 所不具备的
2. **数据库选型扎实**：PostgreSQL + pgvector 提供向量检索 + JSONB + 分区表，单库满足结构化数据与向量检索需求，优于 yuiju 的 MongoDB 方案
3. **成本控制前置**：`BudgetManager` + `CircuitBreaker` 在 LLM 调用层兜底，防止成本失控
4. **Prompt 防护完善**：`PromptGuard` 三层防护（检测 + 消毒 + 包装），安全意识到位
5. **多模态能力**：图像生成 + 视频生成，能力范围超出 yuiju
6. **群聊智能回复**：四层决策策略（@命中 → 关键词 → 启发式 → LLM 判断），群聊体验优于 yuiju

### 9.2 最亟待改进的不足（按优先级，2026-07-13 更新）

1. ~~**完全缺失 CI/CD**（P0）~~ → ✅ 已完成
2. **无 Pre-commit hooks**（P0）：开发者可提交未通过检查的代码
3. ~~**无 AI Coding 规范**（P0）~~ → ✅ 已完成（AGENTS.md + docs/rules/）
4. ~~**全局变量 + `from src.main import` 反模式**（P1）~~ → ✅ 已完成（src/runtime.py）
5. ~~**`main.py` 单文件 3111 行**（P1）~~ → ✅ 已完成（541 行 + 11 路由模块）
6. **核心模块无测试**（P1）：`WorldEngine`、`CharacterTickEngine`、`MessageService` 等核心业务模块零覆盖
7. **配置三源问题**（P1）：`.env` + `config.py` + Redis 覆盖，真相源不明确
8. **无多模型备用源**（P1）：LLM 单一 endpoint，故障时无法自动切换
9. ~~**Prompt 管理分散**（P1）~~ → ✅ 已完成（统一 YAML 外置 + 人设分层）
10. **无 RBAC**（P2）：单一管理员角色，无法支持多用户多角色
11. **无 CONTRIBUTING.md**（P0）：外部贡献者无指南
12. **无 LLM 协定文档**（P1）：LLM 边界散落在架构文档

### 9.3 改进的核心思路

aitown 在**可观测性、数据库、成本控制、安全防护**等"硬实力"上已超过 yuiju，但在**工程规范、自动化、代码组织**等"软实力"上明显落后。改进的核心思路是：

1. **补齐工程基础设施**（CI/CD + pre-commit + 规范文档）：这是最低成本、最高收益的改进
2. **消除架构反模式**（全局变量 + 单文件路由）：解除技术债务，为后续扩展打基础
3. **借鉴 yuiju 的领域设计**（日记系统 + Person Memory + LLM 协定）：丰富记忆系统层次，明确 LLM 边界
4. **保持并强化既有优势**（可观测性 + 成本控制 + 安全防护）：不要在改进中丢失已有优势

通过 P0（1-2 周）补齐工程基础设施，P1（1 个月）消除架构反模式，P2（2-3 个月）丰富领域设计，aitown 可以从"功能完整但工程粗糙"演进为"功能完整且工程严谨"的高质量项目。

### 9.4 改进优先级总览（2026-07-13 更新）

| 优先级 | 总数 | 已完成  | 未完成 | 关键未完成项                                                                   |
| ------ | ---- | ------- | ------ | ------------------------------------------------------------------------------ |
| P0     | 5 项 | 3 项 ✅ | 2 项   | Pre-commit hooks、CONTRIBUTING.md                                              |
| P1     | 8 项 | 5 项 ✅ | 3 项   | 多模型备用源、核心测试覆盖、配置优化                                           |
| P2     | 9 项 | 0 项    | 9 项   | 日记系统、Person Memory、RBAC、JWT 增强、限流覆盖、文档站、ADR、告警、一键部署 |

**完成率：8/22 (36%)**，P0 完成率 3/5 (60%)，P1 完成率 5/8 (62.5%)。

已完成的关键改进：CI/CD、AGENTS.md、docs/rules/、main.py 拆分、全局变量消除、全局异常处理、Prompt 深度增强、多智能体交互、前后端同步。
