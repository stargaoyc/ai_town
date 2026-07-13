# 代码风格规范

> 本文档定义 aitown 项目的 Python 代码风格规范，适配 Python 3.13+ 生态。
>
> 所有贡献者（含 AI Coding Agent）在写入或修改 `packages/backend/` 与 `packages/mcp-servers/` 下的 Python 代码时，必须遵循本规范。
>
> 配套文档：[domain-design-style.md](domain-design-style.md) · [prompt-style.md](prompt-style.md) · [refactor-style.md](refactor-style.md)

---

## 一、六大核心原则

### 1. 主流程优先

**写代码时先让主流程通顺可读，再处理异常分支。** 读代码的人应该能从上到下顺着主流程读懂「这段在做什么」，而不是一开始就被边界判断、防御性代码打断。

| 做法 | 示例 |
|------|------|
| 主流程平铺，异常用 early return / raise 分流 | `if not character: raise CharacterNotFound(...)` 在前，主逻辑在后 |
| 嵌套不超过 3 层 | 超过时拆函数或用 guard clause |
| 禁止「倒置主流程」 | 不要把核心逻辑塞进 `else`，把异常放在 `if` |

```python
# ✅ 主流程平铺
async def tick_character(self, character_id: UUID) -> None:
    character = await self.repo.get(character_id)
    if not character:
        raise CharacterNotFound(character_id)
    if not character.is_active:
        return
    # 主流程从这里开始
    state = await self._perceive(character)
    decision = await self._decide(character, state)
    await self._execute(character, decision)

# ❌ 主流程被埋进 else
async def tick_character(self, character_id: UUID) -> None:
    character = await self.repo.get(character_id)
    if character:
        if character.is_active:
            state = await self._perceive(character)
            # ... 主流程被埋在双层 if 里
    else:
        raise CharacterNotFound(character_id)
```

### 2. 少加概念

**能用现有概念解决的，不引入新概念。** 每引入一个新类/新接口/新中间层，都会增加后续读者的认知负担。

| 禁止 | 说明 |
|------|------|
| 为单一调用方建抽象层 | 只有一个实现的接口，删掉接口直接用类 |
| 为「将来可能扩展」预留钩子 | YAGNI，需要时再加 |
| 自造术语 | 优先复用项目已有的领域语言（见 [domain-design-style.md](domain-design-style.md)） |
| 引入与现有层重复的 Manager/Service/Helper | 先看现有层能否承载 |

```python
# ❌ 为单一调用方建抽象
class ActionExecutorFactory:
    def create(self, action: Action) -> ActionExecutor: ...
# 只有一个 ActionExecutor 实现，Factory 多余

# ✅ 直接用
executor = ActionExecutor(action)
```

### 3. 单一真相源

**同一事实只在一个地方定义。** 状态、配置、常量、规则都不能在多处各写一份。

| 真相源 | 位置 | 禁止 |
|--------|------|------|
| 角色实时状态 | Redis `char:{id}:state` | 在 PG / 内存 / Prompt 里再维护一份"当前状态" |
| 世界实时状态 | Redis `world:state` | 在代码里硬编码场景列表 |
| 场景静态定义 | `configs/world-map.yaml` | 在 Action 注册时重复声明场景属性 |
| Prompt 模板 | `configs/prompts/*.yaml` | 在 Python 代码里内嵌 Prompt 字符串 |
| 常量 | `src/config.py` 的 `settings` | 在业务代码里散落魔法数字 |

```python
# ❌ 状态真相源重复
class CharacterTickEngine:
    async def tick(self, cid: UUID):
        # 从 PG 读了一份状态
        char = await self.char_repo.get(cid)
        # 又从 Redis 读了一份状态
        state = await self.redis.hgetall(f"char:{cid}:state")
        # 两份状态不一致时以谁为准？

# ✅ 实时状态只从 Redis 读
state = await self.redis.hgetall(f"char:{cid}:state")
```

### 4. 显式边界

**函数/模块的输入输出与副作用必须显式。** 读者不该需要读完整个函数实现才知道它会改什么状态。

| 要求 | 做法 |
|------|------|
| 输入显式 | 参数标注类型，不依赖隐式全局状态 |
| 输出显式 | 返回值标注类型，不通过修改入参传出结果 |
| 副作用显式 | 函数名体现副作用（`save_*` / `update_*` / `delete_*`），纯查询用 `get_*` / `list_*` |
| 异常显式 | 自定义异常类型，不抛裸 `Exception` |
| 边界注释 | 跨模块/跨服务调用在 docstring 标注「调用方」「被调用方」 |

```python
# ✅ 副作用显式
async def update_character_state(
    self,
    character_id: UUID,
    changes: dict[str, Any],
) -> None:
    """更新角色实时状态（Redis）。PG 镜像由后台任务异步对齐。"""
    await self.redis.hset(f"char:{character_id}:state", mapping=changes)

# ❌ 副作用隐式
async def process_character(self, character_id: UUID, data: dict) -> dict:
    # 不知道这个函数会不会改状态、改哪里
    ...
```

### 5. 少量重复优于错误抽象

**不要为了消除重复而提前抽象。** 重复的代码可以后续重构，错误的抽象会污染整个代码库。

| 场景 | 做法 |
|------|------|
| 两处代码看起来相似 | 先确认是否真的同语义，再决定是否抽象 |
| 三处以上真重复 | 才考虑提取公共函数 |
| 不确定的抽象 | 留着重复，加 TODO 注释说明 |
| 跨模块的「相似」 | 大概率不该抽象，各自维护 |

```python
# ❌ 错误抽象：把"角色 Tick"和"消息处理"抽象成统一的"AgentRunner"
class AgentRunner:
    async def run(self, agent: Agent) -> None: ...
# 两者流程完全不同，强行统一会让两边都变难读

# ✅ 各自独立
class CharacterTickEngine: ...
class MessageService: ...
```

### 6. 注释解释约束

**注释解释「为什么」，不解释「是什么」。** 代码本身已经说明了「是什么」，注释的职责是补充代码无法表达的业务约束、历史背景、边界原因。

| 该写注释 | 不该写注释 |
|----------|-----------|
| 为什么用 Redis 锁而不是 DB 锁 | `# 获取锁` |
| 为什么这个 Action 不能在雨天执行 | `# 检查天气` |
| 为什么先写 PG 再写 Redis | `# 写入数据库` |
| 历史决策的背景（链接 Issue/PR） | 变量名的重复说明 |

```python
# ✅ 解释约束
# Redis 是实时状态真相源，PG 仅为镜像。
# Action 执行时先写 PG 事务（保证可追溯），提交后再写 Redis（保证实时性）。
# Redis 写失败时由 PG 镜像回灌任务补偿。
await self.action_repo.insert(record)
await self.redis.hset(f"char:{cid}:state", mapping=changes)

# ❌ 重复代码
# 从 Redis 获取角色状态
state = await self.redis.hgetall(f"char:{cid}:state")
```

---

## 二、Python 特定规范

### 2.1 类型标注

**所有函数签名、类属性、模块级变量必须标注类型。** 项目已启用 `mypy --strict`。

| 规则 | 示例 |
|------|------|
| 用 PEP 604 语法（`X \| Y`） | `def get(id: UUID) -> Character \| None` |
| 容器用泛型 | `list[Action]` / `dict[str, Any]` / `set[UUID]` |
| 可选参数用 `\| None = None` | `def tick(self, force: bool \| None = None)` |
| 回调用 `Callable[[T], R]` | `precondition: Callable[[dict], bool] \| None` |
| 不用 `Optional`/`Union`（旧语法） | 禁止 `Optional[Character]` |
| 不用 `Any` 除非真的无法标注 | 用 `Any` 时注释说明原因 |

```python
# ✅
class Action(BaseModel):
    id: str
    precondition: Callable[[dict], bool] | None = None
    executor: Callable[[dict, dict], dict] | None = None
    params_schema: dict | None = None

# ❌
class Action(BaseModel):
    id: str
    precondition: Optional[Callable] = None  # 旧语法 + 缺泛型
```

### 2.2 async / await

**所有 I/O 操作必须异步。** 项目全栈基于 asyncio，禁止同步阻塞调用。

| 规则 | 说明 |
|------|------|
| DB / Redis / HTTP / LLM 调用必须 `async def` | 项目使用 `asyncpg` / `redis.asyncio` / `httpx.AsyncClient` |
| 禁止在 async 函数里调用同步阻塞 I/O | 禁止 `requests.get()` / `time.sleep()` / 同步 `open()` 读大文件 |
| 必须阻塞时用 `asyncio.to_thread` | `await asyncio.to_thread(blocking_fn, arg)` |
| 并发独立调用用 `asyncio.gather` | `await asyncio.gather(self._perceive(cid), self._load_memory(cid))` |
| 信号量限制并发 | `async with self._semaphore: await self.tick_character(cid)` |
| 禁止裸 `asyncio.create_task` 不持有引用 | 任务可能被 GC 回收，应存入集合并追踪 |

```python
# ✅ 并发独立调用
async def _perceive(self, character: Character) -> dict:
    state, memories, world = await asyncio.gather(
        self._get_state(character.id),
        self._get_memories(character.id),
        self._get_world_state(),
    )
    return {**state, "memories": memories, "world": world}

# ❌ 串行等待
async def _perceive(self, character: Character) -> dict:
    state = await self._get_state(character.id)
    memories = await self._get_memories(character.id)
    world = await self._get_world_state()
    return {**state, "memories": memories, "world": world}
```

### 2.3 Pydantic 使用

**所有数据模型用 Pydantic v2 BaseModel。** 禁止用 `dataclass` 承载需要校验的业务数据（`Action`/`DecisionResult`/`ActionResult` 都是 BaseModel）。

| 规则 | 说明 |
|------|------|
| 字段必须标注类型 | `id: str` / `stamina: int = Field(ge=0, le=100)` |
| 用 `Field` 添加约束 | `duration: int = Field(default=10, ge=1, le=1440)` |
| 枚举用 `str, Enum` 混入 | `class ActionCategory(str, Enum):` 保证 JSON 序列化 |
| 不可变模型用 `frozen=True` | `class Config: model_config = ConfigDict(frozen=True)` |
| 禁止 `BaseModel` 内嵌业务逻辑 | 模型只承载数据，业务逻辑放 Service/Engine |
| 配置类用 `pydantic-settings` | `class Settings(BaseSettings):` 从 `.env` 读取 |

```python
# ✅
class DecisionResult(BaseModel):
    action: str
    reason: str
    params: dict = Field(default_factory=dict)
    duration: int | None = None
    plan_changes: list[dict] = Field(default_factory=list)
    proactive_share_intent: bool = False

# ❌ 用 dataclass 承载业务数据
@dataclass
class DecisionResult:
    action: str
    reason: str
    # 缺校验、缺默认值、缺 JSON 序列化
```

### 2.4 structlog 日志

**所有日志用 structlog 结构化输出。** 禁止 `print()` / 标准 `logging` 直接调用。

| 规则 | 示例 |
|------|------|
| 事件名用 `snake_case` | `logger.info("character_tick_completed")` |
| 上下文用 `key=value` | `logger.info("action_executed", character_id=str(cid), action_id=aid)` |
| 禁止 f-string 拼接日志 | 禁止 `logger.info(f"Tick {tick_id} done")` |
| ERROR 必带 `exc_info=True` | `logger.error("redis_failed", error=str(e), exc_info=True)` |
| 敏感信息脱敏 | API Key / JWT / 密码不得出现在日志 |
| 请求级上下文用 `bind_context` | 中间件中 `bind_context(user_id=uid, trace_id=tid)` |

```python
# ✅
logger.info(
    "character_tick_completed",
    character_id=str(character_id),
    duration_ms=elapsed,
    action_id=decision.action,
)

# ❌
logger.info(f"Character {character_id} tick completed in {elapsed}ms, action={decision.action}")
```

### 2.5 其他 Python 规范

| 规则 | 说明 |
|------|------|
| 行宽 120 | `pyproject.toml` 中 `[tool.ruff] line-length = 120` |
| 用 `from __future__ import annotations` | 仅在需要前向引用时 |
| import 顺序 | ruff `isort` 强制：标准库 → 第三方 → 项目内 |
| 禁止 `import *` | 显式导入，除 `__init__.py` 的 re-export |
| 字符串用双引号 | ruff 默认 |
| docstring 用 Google 风格 | `Args:` / `Returns:` / `Raises:` |
| 模块级 docstring | 每个文件首行写模块职责 |

---

## 三、常见坏代码形态

以下七种坏代码形态在 review 时必须被拦截。每种给出「症状—危害—修复」。

### 3.1 过度抽象型

**症状**：为单一实现建接口/工厂/基类，调用链层层转发。

**危害**：读一个简单操作要跳 4-5 个文件，新增字段要改 5 处。

```python
# ❌
class IActionExecutor(ABC):
    @abstractmethod
    def execute(self, action: Action) -> ActionResult: ...

class ActionExecutorBase(IActionExecutor): ...
class DefaultActionExecutor(ActionExecutorBase): ...
class ActionExecutorFactory:
    def create(self) -> IActionExecutor: return DefaultActionExecutor()

# ✅ 直接用类
class ActionExecutor:
    def execute(self, action: Action) -> ActionResult: ...
```

### 3.2 防御性封装型

**症状**：对内部可信代码做大量 None 检查、类型转换、try/except 兜底。

**危害**：掩盖真实错误，让 bug 以「默认值」形式静默传播。

```python
# ❌ 对内部代码防御
def update_state(self, state: dict) -> dict:
    if state is None:
        state = {}
    if not isinstance(state, dict):
        state = {}
    stamina = state.get("stamina", 0) or 0
    if not isinstance(stamina, int):
        stamina = 0
    # ...

# ✅ 信任内部代码，边界由 Pydantic 校验
def update_state(self, state: CharacterState) -> CharacterState:
    return state.replace(stamina=clamp(state.stamina + self.delta))
```

### 3.3 兜底掩盖边界型

**症状**：用 `try: ... except Exception: pass` 或返回默认值掩盖所有异常。

**危害**：问题永远不暴露，线上表现为「角色行为异常」但日志无错。

```python
# ❌ 兜底掩盖
async def get_character(self, cid: UUID) -> Character:
    try:
        return await self.repo.get(cid)
    except Exception:
        return Character(name="unknown")  # 返回假角色，下游不知道出错了

# ✅ 显式异常
async def get_character(self, cid: UUID) -> Character:
    char = await self.repo.get(cid)
    if not char:
        raise CharacterNotFound(cid)
    return char
```

### 3.4 改动扩散型

**症状**：加一个字段要改 5+ 文件：模型、迁移、Repository、Service、API schema、Prompt。

**危害**：每次改动成本高，容易漏改导致不一致。

**修复**：字段集中定义在 Pydantic 模型，Repository/Service 用泛型处理，避免每个字段单独写 `get_xxx`/`set_xxx`。

### 3.5 流程断裂型

**症状**：一个完整业务流程被拆到多个无显式关联的函数，读者无法追踪。

**危害**：调试时无法回放完整流程，bug 难定位。

```python
# ❌ 流程断裂：Tick 逻辑分散在 5 个文件，无主流程入口
# character_tick.py 只调 engine.run()
# engine.py 只调 orchestrator.step()
# orchestrator.py 只调 phase.execute()
# phase.py 只调 service.do()
# 谁也看不出完整流程

# ✅ 主流程显式（参考 character_tick.py）
async def tick_character(self, cid: UUID) -> None:
    state = await self._perceive(cid)
    decision = await self._decide(cid, state)
    await self._execute(cid, decision)
    await self._settle(cid, decision)
```

### 3.6 类型表演型

**症状**：写了完整的类型标注，但类型本身不正确（`Any` 满天飞、`dict` 不标 key/value 类型）。

**危害**：mypy 通过但类型信息无意义，IDE 补全失效。

```python
# ❌ 类型表演
def process(self, data: dict) -> dict:
    config: Any = self.config
    result: dict = {}
    for k, v in data.items():
        result[k] = config.process(v)  # config 是 Any，v 是 Any
    return result

# ✅ 类型有意义
def process(self, episodes: list[MemoryEpisode]) -> list[Reflection]:
    return [self._reflect(ep) for ep in episodes]
```

### 3.7 语义漂移型

**症状**：函数名/变量名与实际行为不符，或同一概念在不同模块叫不同名字。

**危害**：读者按名字理解行为会被误导。

```python
# ❌ 语义漂移
def get_character_state(self, cid: UUID) -> dict:
    # 实际上这个函数会"更新"状态，不只是"get"
    await self.redis.hset(f"char:{cid}:state", mapping={"stamina": 100})
    return await self.redis.hgetall(f"char:{cid}:state")

# ✅ 名字与行为一致
async def reset_character_state(self, cid: UUID) -> dict:
    await self.redis.hset(f"char:{cid}:state", mapping={"stamina": 100})
    return await self.redis.hgetall(f"char:{cid}:state")
```

---

## 四、自查清单

每次提交代码前，逐项自查。任何一项不通过，先修复再提交。

### 4.1 原则自查

- [ ] 主流程是否平铺可读？读者能否从上到下读懂「这段在做什么」？
- [ ] 是否引入了新概念/新抽象？如果有，是否真的有必要（≥3 处真重复）？
- [ ] 同一事实是否只在一个地方定义？状态/配置/常量有没有重复维护？
- [ ] 函数的输入输出与副作用是否显式？读者能否不读实现就知道会改什么状态？
- [ ] 注释是否解释了「为什么」？有没有用注释重复「是什么」？
- [ ] 有没有为了消除重复而提前抽象？不确定的抽象是否留着重复 + TODO？

### 4.2 Python 自查

- [ ] 所有函数签名/类属性是否标注类型？`mypy --strict` 是否通过？
- [ ] 所有 I/O 是否异步？有没有同步阻塞调用混入 async 函数？
- [ ] 数据模型是否用 Pydantic BaseModel？有没有用 dataclass 承载业务数据？
- [ ] 日志是否用 structlog 结构化？有没有 f-string 拼接日志？ERROR 是否带 `exc_info=True`？
- [ ] 行宽是否 ≤ 120？import 顺序是否符合 isort？

### 4.3 坏代码自查

- [ ] 有没有为单一实现建接口/工厂？（过度抽象型）
- [ ] 有没有对内部可信代码做大量 None/类型检查？（防御性封装型）
- [ ] 有没有 `except Exception: pass` 或返回默认值掩盖异常？（兜底掩盖边界型）
- [ ] 加一个字段要改几个文件？（改动扩散型，>5 文件需重构）
- [ ] 完整业务流程能否从一个入口函数追踪到底？（流程断裂型）
- [ ] 类型标注是否有意义？`Any` 是否过多？（类型表演型）
- [ ] 函数名/变量名是否与实际行为一致？（语义漂移型）

### 4.4 项目特定自查

- [ ] LLM 是否被允许直接修改状态？（禁止，必须通过 Action executor）
- [ ] 实时状态是否只从 Redis 读写？有没有在 PG/内存里维护"当前状态"？
- [ ] Prompt 是否外置到 `configs/prompts/*.yaml`？有没有在代码里内嵌 Prompt？
- [ ] Action 是否有 `precondition`？LLM 能否绕过 precondition 选择 Action？

---

## 相关文档

| 主题 | 文档 |
|------|------|
| 领域设计规范 | [domain-design-style.md](domain-design-style.md) |
| Prompt 规范 | [prompt-style.md](prompt-style.md) |
| 重构规则 | [refactor-style.md](refactor-style.md) |
| 可观测性 | [../observability.md](../observability.md) |
| 项目架构 | [../architecture.md](../architecture.md) |
