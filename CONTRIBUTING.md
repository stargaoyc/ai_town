# 贡献指南（CONTRIBUTING）

感谢关注 AI Town！本指南面向所有人类贡献者；AI Coding Agent 请直接阅读 [AGENTS.md](AGENTS.md)——那是 Agent 的入口规范，与本文件共享同一套项目约束。

## 快速开始

```bash
# 1. 前置依赖：Python 3.13+ (uv)、Node 22+ (pnpm 11)、Docker
# 2. 克隆并安装
git clone <repo-url> && cd ai_town
cp .env.example packages/backend/.env   # 按需填写 LLM Key 等配置

# 3. 启动基础设施（PG 在 localhost:5433）
docker compose up -d postgres redis

# 4. 后端
cd packages/backend
uv sync --frozen --extra dev

# 5. 前端
cd ../frontend
pnpm install
```

## 开发工作流

1. **从 main 切分支**：`git checkout -b feat/your-topic`
2. **改动前读规范**：见下方「必读规范」
3. **提交前本地验证**（pre-commit 钩子也会强制执行）：

```bash
# 后端（packages/backend 下）
uv run ruff check src/ tests/ && uv run ruff format src/ tests/
uv run mypy src/ tests/
uv run pytest                 # 单元 + 集成（需 PG:5433 / Redis:6379 可达）

# 前端（packages/frontend 下）
pnpm run lint && pnpm run typecheck && pnpm run build
```

4. **Conventional Commits**：`<type>(<scope>): <摘要>`，详见 [AGENTS.md §5.4](AGENTS.md#54-git-提交规范)

## 必读规范

| 场景 | 必读 |
|------|------|
| 任何后端代码 | [docs/rules/implementation-style.md](docs/rules/implementation-style.md) |
| 任何前端代码 | [docs/rules/frontend-style.md](docs/rules/frontend-style.md) |
| 业务建模 / 新模块 | [docs/rules/domain-design-style.md](docs/rules/domain-design-style.md) |
| LLM Prompt 变更 | [docs/rules/prompt-style.md](docs/rules/prompt-style.md)（Prompt 统一在 `configs/prompts/*.yaml`） |
| 结构调整 | [docs/rules/refactor-style.md](docs/rules/refactor-style.md) |

## 项目核心约定（摘要）

- **状态真相源**：Redis 是实时状态真相源，PG 是镜像；双写顺序「先 PG 事务、commit 后写 Redis」。新增状态字段必须经 `src/core/state_codec.py` 编解码。
- **LLM 边界**：LLM 只产生决策文本与结构化输出，**不直接改状态**；状态写入只发生在执行层。Prompt 一律外置到 `configs/prompts/`，缺失即启动失败。
- **单一真相源**：同一事实只在一处定义——单价表在 `llm/client.py`、Redis 键名常量在使用方模块顶部、默认配置只在 `Settings`。
- **Action 系统**：新行为走 `src/actions/` 注册（含 precondition 与带符号 cost 字段），不在 Tick 里硬编码特判。

完整架构约定见 [AGENTS.md §四](AGENTS.md)。

## 测试要求

- 每个 bug 修复必须带回归测试（先复现再修复）。
- 新增数据访问逻辑需要单元测试；涉及分区表 / 向量检索 / 双库一致性的，补 `tests/integration/` 集成测试（连真实 PG/Redis，服务不可达时自动跳过）。
- 不写快照式无断言测试；测试命名表达行为而非实现。

## 提交什么不该提交什么

| ✅ 提交 | ❌ 不提交 |
|--------|----------|
| 源码 + 对应测试 | `.env`（真实密钥）、`data/` 运行时数据 |
| 文档更新（与代码同步） | IDE 配置、临时脚本、日志 |
| `openapi.json`（API 变更后重新导出） | `node_modules`、`.venv`、`__pycache__` |

## 获取帮助

- 架构问题：[docs/architecture.md](docs/architecture.md)
- 部署问题：[docs/deployment.md](docs/deployment.md)、[docs/docker-deployment.md](docs/docker-deployment.md)
- 已知问题与技术债：[docs/design-improvement-and-fixes.md](docs/design-improvement-and-fixes.md)
