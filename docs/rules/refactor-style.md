# 重构规则

> 本文档定义 aitown 项目的重构时机、步骤、测试先行与回归验证规范。
>
> 重构是开发的一部分，但"为了重构而重构"是被禁止的。每次重构必须有明确的业务或工程收益。
>
> 配套文档：[implementation-style.md](implementation-style.md) · [domain-design-style.md](domain-design-style.md) · [prompt-style.md](prompt-style.md)

---

## 一、何时重构

### 1.1 应该重构的信号

当以下信号出现时，重构是**被推荐**的：

| 信号 | 表现 | 重构方向 |
|------|------|----------|
| 重复 ≥ 3 处 | 同一段逻辑在 3+ 文件出现 | 提取公共函数/类 |
| 函数过长 | 单函数 > 80 行 | 拆分为多个小函数 |
| 参数过多 | 单函数 > 5 个参数 | 封装为参数对象 |
| 嵌套过深 | if/for 嵌套 > 3 层 | guard clause 提前返回 |
| 命名失真 | 函数名与行为不符 | 重命名 |
| 跨层调用 | Infrastructure 直接调 API 层 | 修正依赖方向 |
| 真相源分裂 | 同一数据在多处维护 | 统一到单一真相源 |
| 测试难写 | 写测试需要 mock 大量依赖 | 解耦依赖 |
| 改动扩散 | 加一个字段要改 5+ 文件 | 减少不必要的抽象层 |

### 1.2 必须重构的场景

以下场景**必须**在当前 PR 内完成重构，不得拖延：

| 场景 | 原因 |
|------|------|
| 新增功能时发现现有结构无法承载 | 强行塞入会产生技术债 |
| 修复 bug 时发现根因是结构问题 | 不重构则 bug 会复发 |
| LLM 直接修改状态 | 违反架构约定，必须立即修正 |
| Prompt 内嵌在代码中 | 违反配置真相源约定 |
| 出现循环依赖 | 编译/测试会出问题 |

---

## 二、何时不重构

### 2.1 禁止重构的场景

| 场景 | 原因 |
|------|------|
| 临近发布 | 重构引入的风险无法在发布前充分验证 |
| 没有测试覆盖 | 重构后无法验证行为一致性 |
| 只为"代码好看" | 没有明确业务/工程收益的重构是浪费 |
| 不理解现有代码 | 先读懂再改，不要"边读边重构" |
| 跨多个限界上下文 | 大范围重构应拆分为多个独立 PR |
| 紧急修复中 | 紧急修复只做最小改动，重构另开 PR |

### 2.2 推迟重构的场景

以下场景可以**推迟**重构，但需记录 TODO：

| 场景 | 处理方式 |
|------|----------|
| 重复仅 2 处 | 暂不抽象，等第 3 处出现再重构 |
| 函数略长（50-80 行）但逻辑清晰 | 暂不拆分 |
| 不确定的抽象方向 | 留重复 + TODO 注释，等方向明确再重构 |
| 非核心路径的坏代码 | 优先重构核心路径 |

### 2.3 重构与功能变更分离

**重构 PR 不得混合功能变更。** 一个 PR 要么纯重构（行为不变），要么纯功能（结构不变）。

| PR 类型 | 允许的变更 | 禁止的变更 |
|----------|-----------|-----------|
| 重构 PR | 移动文件、重命名、提取函数、调整结构 | 修改业务逻辑、新增功能 |
| 功能 PR | 新增功能、修改业务逻辑 | 顺便重构无关代码 |

> 例外：新增功能时**必须**做的最小结构调整（如新增 Action 时调整注册表），可与新功能同 PR。

---

## 三、重构步骤

### 3.1 通用重构流程

每次重构遵循以下五步，不得跳过：

```text
1. 理解现状    → 读懂现有代码，确认行为基线
2. 补充测试    → 为现有行为补充测试（若没有）
3. 小步重构    → 每次只改一处，保持测试通过
4. 验证回归    → 运行全量测试 + 手工验证关键路径
5. 更新文档    → 同步更新相关文档
```

### 3.2 步骤详解

#### 步骤 1：理解现状

| 动作 | 说明 |
|------|------|
| 读完所有相关文件 | 不要只读一个文件就动手 |
| 画出当前调用关系 | 用文字或图记录"谁调用谁" |
| 确认行为基线 | 当前代码"实际做什么"（不是"应该做什么"） |
| 找到所有调用方 | grep 调用点，确认重构影响范围 |

#### 步骤 2：补充测试

| 动作 | 说明 |
|------|------|
| 为现有行为写测试 | 测试"当前行为"而非"期望行为" |
| 覆盖正常路径 + 边界 | 正常输入 + 异常输入 + 边界值 |
| 确保测试通过 | 重构前测试必须全绿 |
| 没有测试就不重构 | 这是硬性要求，无例外 |

#### 步骤 3：小步重构

| 规则 | 说明 |
|------|------|
| 每次只改一处 | 不要一次改 5 个文件 |
| 每步后运行测试 | 确保每步都保持测试通过 |
| 提交频繁 | 每步一个 commit，便于回滚 |
| 保持行为不变 | 重构不改变外部行为 |

```python
# ✅ 小步重构示例
# Step 1: 提取函数（行为不变）
def tick_character(self, cid: UUID) -> None:
    state = self._perceive(cid)  # 提取前是内联代码
    decision = self._decide(cid, state)
    self._execute(cid, decision)

# Step 2: 重命名（行为不变）
# Step 3: 调整参数（行为不变）
# ...每步后运行测试
```

#### 步骤 4：验证回归

| 动作 | 说明 |
|------|------|
| 运行全量测试 | `uv run pytest` |
| 运行类型检查 | `uv run mypy` |
| 运行 lint | `uv run ruff check` |
| 手工验证关键路径 | 启动服务，手动走一遍核心流程 |
| 检查日志输出 | 确认日志格式与字段未意外变化 |

#### 步骤 5：更新文档

| 文档 | 更新时机 |
|------|----------|
| `docs/architecture.md` | 架构调整时 |
| `docs/rules/domain-design-style.md` | 领域语言/分层变化时 |
| `docs/character-design.md` | 角色模型变化时 |
| `docs/town-design.md` | 场景/世界地图变化时 |
| 代码内 docstring | 函数签名变化时 |

---

## 四、测试先行

### 4.1 测试金字塔

| 层级 | 占比 | 工具 | 说明 |
|------|------|------|------|
| 单元测试 | 70% | `pytest` | 测试单个函数/类，mock 外部依赖 |
| 集成测试 | 20% | `pytest` + testcontainers | 测试模块间协作，真实 DB/Redis |
| 端到端测试 | 10% | 手工 / API 调用 | 测试完整业务流程 |

### 4.2 重构前的测试要求

| 场景 | 测试要求 |
|------|----------|
| 重构核心路径（World Tick / Character Tick） | 必须有集成测试覆盖 |
| 重构 Action 系统 | 必须有该 Action 的单元测试 |
| 重构记忆系统 | 必须有沉淀 + 检索的集成测试 |
| 重构消息服务 | 必须有 handle_user_message 的集成测试 |
| 重构 Prompt 模板 | 必须验证 LLM 输出格式未变 |
| 重构配置 | 必须验证配置加载正常 |

### 4.3 测试编写规范

| 规则 | 说明 |
|------|------|
| 测试名用 `test_{行为}_{条件}_{预期}` | `test_tick_character_when_inactive_should_skip` |
| 一个测试只验证一个行为 | 不要在一个测试里塞多个断言 |
| 用 AAA 模式 | Arrange / Act / Assert |
| mock 外部依赖 | DB / Redis / LLM / MCP 在单元测试中 mock |
| 集成测试用 testcontainers | 真实 PG + Redis，不用 mock |
| 测试数据自包含 | 不依赖其他测试的执行顺序 |

```python
# ✅ AAA 模式
async def test_eat_action_when_hungry_should_increase_satiety():
    # Arrange
    state = {"satiety": 30, "money": 100}
    action = build_life_actions()[1]  # eat action

    # Act
    changes = apply_cost_fields(state, action)

    # Assert
    assert changes["satiety"] == 60  # 30 + 30
    assert changes["money"] == 50    # 100 - 50
```

### 4.4 测试位置

| 测试类型 | 位置 | 命名 |
|----------|------|------|
| 单元测试 | `packages/backend/tests/` | `test_{module}.py` |
| 集成测试 | `packages/backend/tests/` | `test_{module}_integration.py` |
| MCP 服务器测试 | `packages/mcp-servers/{name}/tests/` | `test_*.py` |

---

## 五、回归验证

### 5.1 验证命令

重构完成后，必须运行以下命令并全部通过：

```bash
# Python 后端
cd packages/backend
uv run ruff check          # lint
uv run mypy                # 类型检查
uv run pytest              # 单元 + 集成测试

# 前端
cd packages/frontend
pnpm run lint              # ESLint
pnpm run type-check        # TypeScript 类型检查
```

### 5.2 关键路径手工验证

除了自动化测试，重构涉及以下路径时必须手工验证：

| 路径 | 验证方式 |
|------|----------|
| World Tick | 启动服务，观察 Tick 日志是否正常推进 |
| Character Tick | 触发角色决策，观察 Action 是否正常执行 |
| 消息处理 | 发送消息，观察角色回复是否正常 |
| 记忆沉淀 | 执行 Action 后，查 PG 确认 MemoryEpisode 写入 |
| 记忆检索 | 触发角色感知，查日志确认记忆检索正常 |
| Redis 状态 | 执行 Action 后，查 Redis 确认状态更新 |

### 5.3 回滚预案

| 规则 | 说明 |
|------|------|
| 重构 PR 必须可独立回滚 | 不依赖其他未合并 PR |
| 小步提交 | 每个 commit 独立可回滚 |
| 保留旧代码一个版本 | 大重构时先保留旧实现，灰度切换后再删 |
| 数据库迁移可回滚 | 每个迁移必须有 `downgrade()` |

---

## 六、常见重构模式

### 6.1 提取函数

**适用**：函数过长，部分逻辑可独立。

```python
# 重构前
async def tick_character(self, cid: UUID) -> None:
    char = await self.repo.get(cid)
    if not char:
        raise CharacterNotFound(cid)
    state = await self.redis.hgetall(f"char:{cid}:state")
    memories = await self.memory_repo.retrieve(cid, limit=10)
    world = await self.redis.hgetall("world:state")
    candidates = self.registry.get_candidates(state)
    # ... 100 行后续逻辑

# 重构后
async def tick_character(self, cid: UUID) -> None:
    char = await self._get_character(cid)
    state = await self._perceive(char)
    decision = await self._decide(char, state)
    await self._execute(char, decision)

async def _perceive(self, char: Character) -> dict:
    state = await self.redis.hgetall(f"char:{char.id}:state")
    memories = await self.memory_repo.retrieve(char.id, limit=10)
    world = await self.redis.hgetall("world:state")
    return {**state, "memories": memories, "world": world}
```

### 6.2 内联函数

**适用**：函数只被调用一次，或函数名不比实现更清晰。

```python
# 重构前
def is_hungry(state: dict) -> bool:
    return state.get("satiety", 0) < 30

if is_hungry(state):
    # ...

# 重构后（若只调用一处）
if state.get("satiety", 0) < 30:
    # ...
```

### 6.3 重命名

**适用**：名字与行为不符。

```python
# 重构前
def process_data(self, data: dict) -> dict:
    # 实际上是"更新角色状态"
    await self.redis.hset(...)

# 重构后
async def update_character_state(self, cid: UUID, changes: dict) -> None:
    await self.redis.hset(f"char:{cid}:state", mapping=changes)
```

### 6.4 移动类/函数

**适用**：放错了层或模块。

```python
# 重构前：Action 定义放在 core/ 下
# src/core/actions/life.py

# 重构后：移到 actions/ 下
# src/actions/life.py
```

### 6.5 提取基类

**适用**：多个类有真重复（≥3 处）的逻辑。

```python
# 重构前：WeatherEvolution / SceneEvolution 各自实现 precondition 逻辑
# 重构后：提取 Evolution 基类
class Evolution(ABC):
    @abstractmethod
    async def precondition(self, ctx: WorldAdvanceContext) -> bool: ...
    @abstractmethod
    async def evolve(self, ctx: WorldAdvanceContext) -> dict: ...
```

> 注意：提取基类前确认是否真的需要多态，还是只是"看起来像"。参见 [implementation-style.md §1.5](implementation-style.md#5-少量重复优于错误抽象)。

### 6.6 替换条件分支

**适用**：大量 if/elif 按类型分支。

```python
# 重构前
def apply_evolution(self, type: str, ctx):
    if type == "weather":
        return self._evolve_weather(ctx)
    elif type == "scene":
        return self._evolve_scene(ctx)
    elif type == "resource":
        return self._evolve_resource(ctx)

# 重构后：用注册表
class EvolutionRegistry:
    def __init__(self):
        self._evolutions: list[Evolution] = []
    def register(self, e: Evolution):
        self._evolutions.append(e)
    async def run_all(self, ctx):
        for e in self._evolutions:
            if await e.precondition(ctx):
                await e.evolve(ctx)
```

---

## 七、重构检查清单

每次重构 PR 提交前，逐项自查：

### 7.1 必要性检查

- [ ] 是否有明确的重构信号（重复 ≥3 处 / 函数过长 / 嵌套过深等）？
- [ ] 重构收益是否大于风险？
- [ ] 是否与功能变更分离（纯重构 PR）？

### 7.2 测试检查

- [ ] 重构前是否有测试覆盖？
- [ ] 测试是否在重构前全绿？
- [ ] 重构后测试是否仍全绿？
- [ ] 是否补充了新路径的测试？

### 7.3 行为不变检查

- [ ] 外部行为是否保持不变？
- [ ] 日志输出格式是否未意外变化？
- [ ] API 响应格式是否未变化？
- [ ] Prompt 输出是否未变化？

### 7.4 验证检查

- [ ] `uv run ruff check` 是否通过？
- [ ] `uv run mypy` 是否通过？
- [ ] `uv run pytest` 是否通过？
- [ ] 关键路径是否手工验证？

### 7.5 文档检查

- [ ] 相关文档是否同步更新？
- [ ] 代码内 docstring 是否更新？
- [ ] 重构涉及的配置是否更新？

### 7.6 回滚检查

- [ ] PR 是否可独立回滚？
- [ ] 每个 commit 是否独立可回滚？
- [ ] 数据库迁移是否有 `downgrade()`？

---

## 相关文档

| 主题 | 文档 |
|------|------|
| 代码风格规范 | [implementation-style.md](implementation-style.md) |
| 领域设计规范 | [domain-design-style.md](domain-design-style.md) |
| Prompt 规范 | [prompt-style.md](prompt-style.md) |
| 开发指南 | [../development-guide.md](../development-guide.md) |
| 架构总览 | [../architecture.md](../architecture.md) |
