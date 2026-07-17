# AGENTS.md

> 本文件是 AI Coding Agent（含 Trae、Cursor、Copilot 等）在本项目执行任务时的入口规范。
>
> **任何 AI Agent 在修改本项目代码前，必须先读完本文件。**
>
> 项目规范优先于 AI 的通用编码习惯。当两者冲突时，以本文件及相关规范文档为准。

---

## 一、代码风格入口

修改代码前，先阅读以下规范文档：

| 规范 | 文档 | 适用范围 |
|------|------|----------|
| 代码风格规范 | [docs/rules/implementation-style.md](docs/rules/implementation-style.md) | 所有 Python 代码 |
| 领域设计规范 | [docs/rules/domain-design-style.md](docs/rules/domain-design-style.md) | 业务代码组织 |
| Prompt 规范 | [docs/rules/prompt-style.md](docs/rules/prompt-style.md) | 所有 LLM Prompt |
| 重构规则 | [docs/rules/refactor-style.md](docs/rules/refactor-style.md) | 任何结构变更 |

### 核心原则速查

1. **主流程优先**：先让主流程通顺可读，再处理异常分支
2. **少加概念**：能用现有概念解决的不引入新概念
3. **单一真相源**：同一事实只在一个地方定义
4. **显式边界**：函数的输入输出与副作用必须显式
5. **少量重复优于错误抽象**：不要为消除重复而提前抽象
6. **注释解释约束**：注释解释「为什么」，不解释「是什么」

详见 [docs/rules/implementation-style.md §一](docs/rules/implementation-style.md#一六大核心原则)。

---

## 二、AI Coding 执行协议

### 2.1 写代码前必须说明技术方案

**禁止直接开始写代码。** 在修改任何文件前，AI Agent 必须先输出技术方案，包含：

| 要素 | 说明 |
|------|------|
| 改动目标 | 这次改动要解决什么问题 |
| 影响范围 | 涉及哪些文件/模块 |
| 技术方案 | 用什么方式实现（新增类/修改函数/调整配置） |
| 真相源影响 | 是否涉及 Redis/PG/配置文件的读写 |
| 副作用 | 改动会产生什么副作用 |
| 测试计划 | 如何验证改动正确性 |

### 2.2 需求不明确必须询问

**禁止自行猜测需求。** 遇到以下情况，必须先向用户确认：

| 场景 | 示例 |
|------|------|
| 需求有歧义 | "优化性能"未指明哪段代码 |
| 多种实现方式 | 可新增 Action 也可修改现有 Action |
| 涉及架构决策 | 是否引入新依赖、是否新增模块 |
| 破坏性变更 | 修改数据库 schema、修改 API 响应格式 |
| 超出任务范围 | 发现"相关 bug"但不在当前任务内 |

### 2.3 项目规范优先于 AI 通用习惯

**AI 的通用编码习惯不得覆盖项目规范。** 常见冲突：

| AI 通用习惯 | 项目规范 | 项目做法 |
|-------------|----------|----------|
| 用 `Optional[X]` | 用 PEP 604 语法 | `X \| None` |
| 用 `dataclass` 承载业务数据 | 用 Pydantic BaseModel | `class X(BaseModel):` |
| 用 `print()` 调试 | 用 structlog | `logger.info(...)` |
| 内嵌 Prompt 字符串 | 外置 YAML | `configs/prompts/*.yaml` |
| 用 `try: except: pass` 兜底 | 显式抛异常 | `raise CharacterNotFound(...)` |
| 为单一实现建接口 | 直接用类 | 不建 `IFactory` |
| 用 `logging` 标准库 | 用 structlog | `from structlog import get_logger` |
| 同步阻塞 I/O | 全异步 | `async def` + `await` |
| 用 `Optional`/`Union` | 用 `X \| Y` | PEP 604 |
| 写 docstring 解释「是什么」 | 解释「为什么」 | 注释补充约束 |

### 2.4 不新增防御性逻辑/兜底/fallback

**禁止在本次改动中新增以下代码**（除非用户明确要求）：

| 禁止 | 原因 |
|------|------|
| `try: ... except Exception: pass` | 兜底掩盖边界 |
| `if x is None: x = default` | 对内部可信代码防御 |
| `if not isinstance(x, T): x = T()` | 类型检查应由 Pydantic 完成 |
| 为"将来可能扩展"预留接口 | YAGNI |
| 为"可能为 None"的内部返回值加兜底 | 应修正返回值类型 |
| 在 LLM 调用处加 `try/except` 返回默认回复 | 应由 CircuitBreaker 处理 |

> 详见 [docs/rules/implementation-style.md §三](docs/rules/implementation-style.md#三常见坏代码形态)。

---

## 三、项目约束

### 3.1 技术栈与 Monorepo 结构

本项目是 **uv + pnpm monorepo**：

| 包 | 路径 | 包管理 | 说明 |
|----|------|--------|------|
| 后端 | `packages/backend/` | uv (Python 3.13+) | FastAPI + LangChain + SQLAlchemy + Redis |
| 前端 | `packages/frontend/` | pnpm | React 19 + TanStack Router + Vite |

### 3.2 各包职责

| 包 | 职责 | 禁止 |
|----|------|------|
| `backend` | 世界引擎、角色 Tick、消息服务、API、可观测性 | 在 API 层写业务逻辑 |
| `frontend` | Web Dashboard、监控页面、角色管理界面 | 在前端写业务规则 |

### 3.3 Prompt 维护位置

**所有 Prompt 模板必须外置到 `configs/prompts/*.yaml`。** 禁止在 Python 代码里内嵌 Prompt 字符串。

现有 Prompt 文件：

| 文件 | 用途 |
|------|------|
| `configs/prompts/chat.yaml` | 角色回复用户消息 |
| `configs/prompts/decision.yaml` | 角色 Action 决策 |
| `configs/prompts/reflection.yaml` | 角色反思生成 |

详见 [docs/rules/prompt-style.md](docs/rules/prompt-style.md)。

### 3.4 配置真相源

| 配置类型 | 真相源 | 说明 |
|----------|--------|------|
| 应用配置 | `.env` + `src/config.py` | pydantic-settings 读取 |
| 角色卡 | `configs/characters/*.yaml` | 多角色配置 |
| 世界地图 | `configs/world-map.yaml` | 场景与连通矩阵 |
| 事件 | `configs/events.yaml` | 节日与事件 |
| Prompt | `configs/prompts/*.yaml` | LLM 模板 |

---

## 四、架构约定

### 4.1 状态真相源

| 数据 | 真相源 | 说明 |
|------|--------|------|
| **角色实时状态** | **Redis** `char:{id}:state` | 唯一真相源，PG 仅镜像 |
| **世界实时状态** | **Redis** `world:state` | 唯一真相源，PG 仅快照 |
| 角色档案 | PG `characters` 表 | 静态身份信息 |
| 行为历史 | PG `action_records` 表 | 不可变事实记录 |
| 记忆事实 | PG `memory_episodes` + pgvector | 不可变经历事件 |

> **Redis 是实时状态真相源，PG 保存历史与可追溯记录。** Action 执行时先写 PG 事务，再写 Redis（事务提交后），失败时由 PG 镜像回灌。

### 4.2 Action 约定

| 约定 | 说明 |
|------|------|
| **Action 必须有 precondition** | `(state: dict) -> bool`，由代码过滤候选，LLM 不能绕过 |
| **Action executor 不直接写状态** | 返回 `new_state` 字典，由执行层统一写入 |
| **LLM 不直接修改状态** | LLM 只能在候选 Action 中选择，状态变更由 executor + 执行层完成 |
| **Action 按场景组织** | `scene + activity` 绑定，不在咖啡店不能执行咖啡店专属 Action |
| **资源字段符号约定** | `energy_cost` 正=恢复负=消耗；`money_cost` 正=花费 |

### 4.3 LLM 边界

| LLM 能做 | LLM 不能做 |
|----------|-----------|
| 在候选 Action 中选择 | 直接修改 Redis/PG 状态 |
| 给出决策理由 | 判断未进候选的 Action |
| 生成自然语言回复 | 暴露 Action/schema/字段名等工程概念 |
| 整理日记、总结记忆 | 替代 MemoryEpisode 作为事实真相源 |
| 建议计划变更 | 直接写入 plans 表（由业务流程执行） |

### 4.4 分层依赖

```text
API 层 → Service 层 → Core 层 → Infrastructure 层 → Cross-cutting 层
```

- 禁止循环依赖
- 禁止跨层调用（如 Infrastructure 直接调 API）
- 禁止在 Repository 里写业务规则

详见 [docs/rules/domain-design-style.md §三](docs/rules/domain-design-style.md#三分层落点)。

---

## 五、验证命令

修改代码后，必须运行以下命令并全部通过：

### 5.1 Python 后端

```bash
cd packages/backend
uv run ruff check          # lint 检查
uv run mypy                # 类型检查（strict 模式）
uv run pytest              # 单元 + 集成测试
```

### 5.2 前端

```bash
cd packages/frontend
pnpm run lint              # oxlint 检查
pnpm run typecheck         # TypeScript 类型检查
```

### 5.3 完整验证（提交前）

```bash
# 后端
cd packages/backend && uv run ruff check && uv run mypy && uv run pytest

# 前端
cd packages/frontend && pnpm run lint && pnpm run typecheck
```

> 任何一项不通过，不得提交。修复后重新运行全部命令。

---

## 六、关键文档索引

| 主题 | 文档 |
|------|------|
| 代码风格规范 | [docs/rules/implementation-style.md](docs/rules/implementation-style.md) |
| 领域设计规范 | [docs/rules/domain-design-style.md](docs/rules/domain-design-style.md) |
| Prompt 规范 | [docs/rules/prompt-style.md](docs/rules/prompt-style.md) |
| 重构规则 | [docs/rules/refactor-style.md](docs/rules/refactor-style.md) |
| 架构总览 | [docs/architecture.md](docs/architecture.md) |
| 角色设计 | [docs/character-design.md](docs/character-design.md) |
| 小镇设计 | [docs/town-design.md](docs/town-design.md) |
| 世界引擎 | [docs/world-engine.md](docs/world-engine.md) |
| Action 系统 | [docs/action-system.md](docs/action-system.md) |
| 记忆系统 | [docs/memory-system.md](docs/memory-system.md) |
| 可观测性 | [docs/observability.md](docs/observability.md) |
| 配置参考 | [docs/config-reference.md](docs/config-reference.md) |
| yuiju 对比 | [docs/yuiju-comparison.md](docs/yuiju-comparison.md) |
| 开发指南 | [docs/development-guide.md](docs/development-guide.md) |
