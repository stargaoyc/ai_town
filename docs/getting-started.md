# 新手学习指南

> 本文档面向刚接触 AI Town 项目的完全新手。假设你已经对 AI Agent、PostgreSQL、FastAPI 有基础了解，但不熟悉本项目。
>
> 阅读完本文档后，你将能够：在本地完整启动项目、创建一个 AI 角色、观察它在小镇里自主生活、并通过 Web/QQ 与它对话。
>
> 预计阅读时间：40 分钟。预计动手实操时间：2–4 小时（取决于网络与机器性能）。

---

## 目录

- [一、项目是什么？](#一项目是什么)
- [二、核心概念解析（新手必读）](#二核心概念解析新手必读)
- [三、环境准备（手把手教学）](#三环境准备手把手教学)
- [四、后端启动详解](#四后端启动详解)
- [五、前端启动](#五前端启动)
- [六、QQ 机器人接入（可选但推荐）](#六qq-机器人接入可选但推荐)
- [七、角色创建与配置](#七角色创建与配置)
- [八、与世界交互](#八与世界交互)
- [九、可观测性配置（可选）](#九可观测性配置可选)
- [十、常见问题排查（FAQ）](#十常见问题排查faq)
- [十一、进阶学习路径](#十一进阶学习路径)
- [十二、开发工具推荐](#十二开发工具推荐)

---

## 一、项目是什么？

### 1.1 用大白话解释

**AI Town 是一个"AI 角色生活在虚拟小镇"的项目。**

想象一下：有一个二次元风格的小镇，镇上住着一群 AI 角色——17 岁的高中生"奏"、活泼的"小春"、温柔的"凛"……他们每个人都有自己的名字、年龄、职业、性格、爱好、背景故事。他们不是被关在聊天框里等你提问的工具人，而是**真的"生活"在这个小镇里**：

- 早上 7 点，奏会在神社醒来，弹一会儿钢琴；
- 上午 9 点，她走去学校上课；
- 中午 12 点，她和同学小春在咖啡店吃午饭，聊起昨天的观星；
- 下午 5 点放学后，她去书店翻一本乐谱；
- 晚上 10 点，她回家写日记，然后睡觉。

而**你**作为一个真实用户，可以：

- 通过 Web 网页观察整个小镇的运转；
- 通过 QQ 私聊或群聊找某个角色聊天；
- 角色也会**主动**找你分享："今天我在公园看到了流星，好开心！"

### 1.2 核心理念

项目的一句话定位写在 README 里：

> **不做"随叫随到的AI助手"，而是做一群有自己生活的"人"。**

这意味着：

| 传统聊天机器人         | AI Town 的角色                       |
| ---------------------- | ------------------------------------ |
| 你问一句，它答一句     | 你不找它，它也在生活                 |
| 每次对话都是"临时人设" | 角色有持续的记忆，记得你昨天说过的话 |
| 没有自己的需求         | 角色有体力、饥饿、情绪、社交需求     |
| 不会主动找你           | 角色会主动分享日常                   |
| 状态less               | 状态ful，行为长期一致且会演化        |

### 1.3 与传统聊天机器人的区别

**区别 1：状态ful，不是 Stateless**

传统 ChatGPT 调用是无状态的——你发一条 prompt，它返回一段文字，下一次调用它什么都不记得（除非你把历史塞进 context）。AI Town 的角色状态**持久化在 PostgreSQL + Redis 中**：体力、饱腹度、情绪、当前位置、正在做的事、所有记忆、所有计划、所有关系……都落库。

**区别 2：世界持续运行，不依赖用户消息**

传统机器人的"思考"是被动的——只有用户发消息才触发。AI Town 有一个 **World Tick**（世界心跳）和 **Character Tick**（角色心跳）的后台循环，即使用户全部下线，角色依然在小镇里吃饭、睡觉、工作、聊天、移动。

**区别 3：记忆 + 反思 + 规划**

传统机器人最多塞一个"system prompt + 最近 N 条历史"。AI Town 角色有三层认知：

- **记忆流**：记得经历过的每件事（用 pgvector 做向量检索）
- **反思**：定期从记忆中归纳高层认知（"小春最近似乎有点累"）
- **规划**：有自己的长期计划（"完成一首钢琴曲"）

**区别 4：行为决策由 LLM + 代码共同完成**

LLM **不是**状态真相源——它只负责"决策"（在候选 Action 中选一个）。真正的状态变更（扣体力、改位置、写记忆）由**代码在单一 PG 事务中**执行，保证可追溯、可回滚。

### 1.4 项目能做什么（功能清单）

| 功能模块         | 说明                                                            |
| ---------------- | --------------------------------------------------------------- |
| **多角色共居**   | 支持 10–50 个 AI 角色同时在小镇生活                             |
| **世界持续运行** | World Tick 每 30 秒推进一次世界状态（时间、天气、场景）         |
| **角色自主行为** | Character Tick 让角色定期"思考"下一步做什么                     |
| **记忆系统**     | 角色记住每件事，用 pgvector 做语义检索                          |
| **反思系统**     | 角色定期从记忆中归纳高层认知                                    |
| **规划系统**     | 角色有长期/短期计划，行为围绕计划展开                           |
| **Action 系统**  | 结构化的行为决策（移动、生活、工作、社交）                      |
| **小镇场景**     | 家、学校、咖啡店、书店、图书馆、公园、神社……                    |
| **移动系统**     | 角色在场景间移动，有路径规划和耗时                              |
| **作息系统**     | 角色有早鸟/夜猫子类型，不同时段活跃度不同                       |
| **动态耗时**     | 天气、拥挤度、体力都会影响行为耗时                              |
| **关系图谱**     | 角色之间有关系（朋友/恋人/同学），互动会改变关系强度            |
| **主动分享**     | 角色会主动找你聊天分享日常（早安、新发现等）                    |
| **多端触达**     | Web Dashboard、QQ（OneBot）、飞书（Lark）                       |
| **群聊智能回复** | 在 QQ 群里智能判断是否回复（不是只 @ 才回）                     |
| **本地工具调用** | 商店模拟、知识库、社交系统、世界查询、自省（进程内 async 函数） |
| **可观测性**     | OpenTelemetry + Langfuse + Prometheus + Grafana + Jaeger + Loki |
| **成本控制**     | LLM 日预算 + 熔断器 + 速率限制                                  |
| **角色卡导入**   | YAML 格式角色卡，支持批量导入                                   |

---

## 二、核心概念解析（新手必读）

这一章用最通俗的语言解释项目里的核心概念。**如果你跳过这一章，后面的内容会看不懂。**

### 2.1 World Tick（世界心跳）

**一句话**：每隔几秒钟，整个虚拟世界的"时间"就往前走一格。

**详细解释**：

想象你在一个游戏里，"游戏时间"不是真实的时钟，而是由一个叫 World Tick 的循环驱动的。每过 30 秒（可通过 `WORLD_TICK_SECONDS` 配置），World Tick 就执行一次，做这些事：

1. **推进世界时间**：把虚拟时钟往前走一段（比如走 10 分钟，由 `WORLD_TICK_MINUTES` 配置）
2. **更新天气**：每隔一定 Tick 数（`WORLD_WEATHER_INTERVAL=60`）随机变化天气
3. **更新场景状态**：某些场景有时间窗口（学校 8–17 点开放）
4. **资源演化**：角色的体力、饱腹度自然衰减
5. **持久化**：把世界状态写到 Redis（`world:state` 哈希表），定期写差分事件到 PostgreSQL 的 `world_events` 表

**关键点**：World Tick 是**单实例运行**的——通过 Redis 分布式锁选主（`world:tick:leader` 锁，TTL 30 秒），只有持锁的实例才会推进世界，避免多实例并发导致时间跳秒。

**查看方式**：

```bash
curl http://localhost:8000/api/v1/world
```

返回示例：

```json
{
  "tick_id": 128,
  "world_time": "08:30",
  "weather": "sunny",
  "temperature": 22,
  "active_characters": 4
}
```

### 2.2 Character Tick（角色心跳）

**一句话**：每个角色定期"思考"一下：我现在该做什么？

**详细解释**：

Character Tick 是角色的"大脑循环"。每 30 秒（`CHARACTER_TICK_SECONDS=30`），系统会对所有活跃角色（`is_active=true`）逐个执行一次 Tick。一次 Character Tick 包含**五个阶段**（这是整个项目最核心的设计）：

```
① 感知环境
   ├─ 读取角色状态（位置/精力/情绪/当前行为）
   ├─ 读取世界状态（时间/天气/场景）
   ├─ 读取周围角色（同位置的其他角色）
   └─ 记忆检索（从 pgvector 检索 Top-K 相关记忆）
        ↓
② 候选 Action 过滤
   └─ 遍历所有 Action，检查 precondition，生成候选列表
        ↓
③ LLM 决策
   ├─ 输入: 角色状态 + 世界状态 + 候选列表 + 检索到的记忆
   ├─ 模型: strong 类型（gpt-4o，复杂决策）
   └─ 输出: 结构化决策 { action, reason, params, duration }
        ↓
④ Action 执行（单一 PG 事务）
   ├─ 更新 Redis 状态（位置/精力/行为）
   ├─ 写入 action_records 表
   └─ 生成 memory_episodes 存入 pgvector
        ↓
⑤ 记忆沉淀与反思触发
   ├─ 检查是否触发反思（如记忆数量达到阈值）
   └─ 检查是否需要调整计划
```

**关键点**：

- **并发控制**：用 `asyncio.Semaphore` 限制并发 Tick 数（`CHARACTER_MAX_CONCURRENT=10`）
- **分布式锁**：每个角色有 `char:tick:lock:{character_id}` 锁，避免同一角色被并发 Tick
- **限流退避**：遇到 LLM 429 错误会自动退避（间隔翻倍，最大 10 倍）

### 2.3 记忆系统（Memory Episodes）

**一句话**：角色会记住经历过的每件事，并且能"回忆起"相关的记忆。

**详细解释**：

每条记忆是一条 `MemoryEpisode` 记录，存在 PostgreSQL 的 `memory_episodes` 表中。每条记忆包含：

| 字段           | 说明                                             |
| -------------- | ------------------------------------------------ |
| `id`           | UUID v7 主键（时间有序）                         |
| `character_id` | 所属角色                                         |
| `content`      | 记忆内容（自然语言，如"和小春在咖啡店吃了午饭"） |
| `embedding`    | 向量（1536 维，用 text-embedding-3-small 生成）  |
| `importance`   | 重要性分数（1–10，影响检索权重）                 |
| `timestamp`    | 发生时间                                         |
| `is_reflected` | 是否已被反思吸收                                 |

**检索机制**：当角色需要决策时，会把当前情境编码成向量，用 pgvector 的 HNSW 索引做 Top-K 检索，找到最相关的几条记忆。检索不是纯向量相似度，而是混合排序：

```
final_score = 向量相似度 × 0.6 + 重要性 × 0.05 + 时间衰减 × (-0.05)
```

**异步向量化**：记忆写入后并不立即生成向量，而是由后台的 `EmbeddingWorker` 每 5 秒轮询一次，批量生成向量（节省 API 调用）。

### 2.4 反思系统（Reflection）

**一句话**：角色定期从一堆记忆里"总结"出几条高层认知。

**详细解释**：

如果角色只靠"原始记忆"决策，那它永远只能看到具体事件，无法形成"认知"。反思系统让角色定期（比如记忆数达到 100 条时）调用 LLM 做一次总结：

- 输入：最近的一批记忆
- 输出：几条高层反思，如"小春最近似乎有点累，我应该关心她"、"我最近钢琴进步很慢，需要加练"

反思存在 `reflections` 表，并且会被标记到对应的记忆源（`reflection_sources` 表）。反思本身也会被向量化，参与后续的记忆检索。

### 2.5 规划系统（Plans）

**一句话**：角色有自己的长期/短期计划，行为会围绕计划展开。

**详细解释**：

每个角色可以有多个 Plan，存在 `plans` 表中。计划字段：

| 字段          | 说明                                            |
| ------------- | ----------------------------------------------- |
| `type`        | `long_term` / `short_term`                      |
| `title`       | 计划标题（如"完成一首钢琴曲"）                  |
| `description` | 详细描述                                        |
| `status`      | `active` / `completed` / `paused` / `cancelled` |
| `priority`    | 1–5，影响决策权重                               |
| `progress`    | 0–100                                           |
| `deadline`    | 截止时间                                        |

**与 Action 的关系**：在 Character Tick 的 LLM 决策阶段，候选 Action 会连同当前活跃计划一起喂给 LLM，LLM 会优先选择能推进计划的 Action。

### 2.6 Action 系统

**一句话**：Action 是角色能做的"原子行为"，LLM 只能在候选 Action 里选，不能瞎编。

**详细解释**：

Action 是这个项目最精妙的设计——**LLM 不能直接执行任意操作**，它只能从一个候选列表里选一个。这保证了：

1. **安全性**：LLM 不会"幻觉"出危险操作
2. **可追溯**：每个 Action 都有明确的 precondition、cost、effect
3. **可测试**：Action 是代码，可以单元测试

一个 Action 定义包含：

```python
class Action:
    id: str                    # 唯一标识，如 "play_piano"
    name: str                  # 显示名，如 "弹钢琴"
    category: ActionCategory   # MOVE / LIFE / WORK / SOCIAL / SPECIAL
    scene: str | None          # 所需场景（如 "shrine"），None 表示任意
    duration_minutes: int      # 基础耗时（虚拟分钟）
    energy_cost: int           # 体力变化（正=恢复，负=消耗）
    satiety_cost: int          # 饱腹度变化
    money_cost: int            # 金钱消耗（正数=花费）
    precondition: Callable     # 前置条件检查函数
    executor: Callable         # 执行器函数
```

**候选过滤**（`ActionRegistry.get_candidates`）做三件事：

1. 检查 precondition（如"睡觉"只能在 home 场景）
2. 检查场景匹配（如"弹钢琴"只能在 shrine）
3. 检查资源（如"买东西"需要 money 足够）

项目内置的 Action 分类：

- **MOVE**：移动（`move_to_scene`）
- **LIFE**：生活（sleep、eat、relax、play_piano、read_book……）
- **WORK**：工作（study、work_parttime）
- **SOCIAL**：社交（chat_with、give_gift）
- **SPECIAL**：特殊（use_phone、travel）

### 2.7 主动分享（Proactive Sharing）

**一句话**：角色会主动找你聊天，分享它刚才发生的事。

**详细解释**：

传统机器人永远是被动的——你不找它，它不找你。AI Town 的角色会**主动**发起分享，触发场景包括：

1. 角色完成重要 Action（如获得新物品、达成里程碑）
2. 角色情绪强烈变化（兴奋/沮丧）
3. 角色与他人发生有趣互动
4. 定时日常分享（早安/晚安/吃饭）

**防刷屏机制**：

- 同一角色对同一用户的分享冷却：1 小时（`SHARE_COOLDOWN_SECONDS=3600`）
- 单角色每日最大分享次数：5 次（`DAILY_SHARE_LIMIT=5`）

**推送通道**：通过 WebSocket 推送给 Web 用户，通过 OneBot 的 `send_private_msg` 推送给 QQ 用户。

### 2.8 群聊智能回复

**一句话**：在 QQ 群里，角色不是只被 @ 才回复，而是会"判断"是否该回复。

**详细解释**：

通过 `ONEBOT_GROUP_AT_ONLY` 配置控制：

- **`false`（默认，智能回复模式）**：读取群内所有消息，由 LLM 判断"这条消息我该不该回？该说什么？"。适合活跃度低的群。
- **`true`（仅 @ 回复模式）**：只有被 @ 时才回复。适合活跃度高的群，省 token。

**群-角色映射**：通过 `ONEBOT_GROUP_CHARACTER_MAP` 配置，可以让不同的群绑定不同的角色：

```json
{ "123456789": "uuid-of-character-a", "987654321": "uuid-of-character-b" }
```

未配置的群使用 `ONEBOT_DEFAULT_CHARACTER_ID` 指定的默认角色。

---

## 三、环境准备（手把手教学）

### 3.1 必备软件安装

#### 3.1.1 Python 3.13+ 安装

**Windows**：

1. 访问 https://www.python.org/downloads/windows/
2. 下载 "Windows installer (64-bit)" 的 3.13+ 版本
3. 安装时**务必勾选** "Add Python to PATH"
4. 验证：

```powershell
python --version
# 输出应为：Python 3.13.x
```

**Linux (Ubuntu/Debian)**：

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt install -y python3.13 python3.13-venv python3.13-dev
python3.13 --version
```

**macOS (Homebrew)**：

```bash
brew install python@3.13
python3.13 --version
```

#### 3.1.2 uv 包管理器安装

`uv` 是一个用 Rust 写的 Python 包管理器，比 pip/poetry 快 10–100 倍，本项目用它管理后端依赖。

**所有平台通用安装命令**：

```powershell
# Windows PowerShell / macOS / Linux 通用
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

或：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

验证：

```powershell
uv --version
# 输出应为：uv 0.x.x
```

#### 3.1.3 Node.js 22+ 安装

**Windows**：

1. 访问 https://nodejs.org/en/download/
2. 下载 LTS 版本（22.x+）的 Windows Installer
3. 安装时勾选 "Add to PATH"

**Linux (Ubuntu)**：

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

**macOS**：

```bash
brew install node@22
```

验证：

```powershell
node --version
# 输出应为：v22.x.x
npm --version
```

#### 3.1.4 pnpm 安装

```powershell
npm install -g pnpm@11
pnpm --version
# 输出应为：11.x.x
```

#### 3.1.5 PostgreSQL 18 安装

> **重要**：PostgreSQL 必须是 18 或更高版本，且必须安装 `pgvector`、`pg_trgm` 三个扩展。

**推荐方式：使用项目内置的 Docker 镜像（最省事）**

项目已经提供了一个预装所有扩展的 Docker 镜像，配置文件在 `docker/postgres/Dockerfile`。这是最推荐的方式：

```powershell
# 在项目根目录执行
docker compose -f docker-compose.infra.yml up -d postgres
```

这会启动一个 PostgreSQL 18 容器，已安装 pgvector + pg_trgm，端口 5432，用户名 `ai_town`，密码 `password`，数据库名 `ai_town`。

**Windows 手动安装（不推荐，扩展编译麻烦）**：

1. 访问 https://www.postgresql.org/download/windows/
2. 下载 18.x 版本安装包
3. 安装时记住密码

**Linux (Ubuntu)**：

````bash
# 添加 PostgreSQL 官方源
sudo sh -c 'echo "deb https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg
sudo apt update
sudo apt install -y postgresql-18 postgresql-server-dev-18

**macOS**：
```bash
brew install postgresql@18
brew install pgvector
````

#### 3.1.6 Redis 安装

**推荐方式：Docker**

```powershell
docker run -d --name aitown-redis -p 6379:6379 redis:8.0-alpine
```

**Windows 原生**：Redis 官方不支持 Windows，但可以用 Memurai 或 WSL2。

**Linux**：

```bash
sudo apt install -y redis-server
sudo systemctl start redis-server
```

**macOS**：

```bash
brew install redis
brew services start redis
```

验证：

```powershell
redis-cli ping
# 输出应为：PONG
```

#### 3.1.7 Git 安装

**Windows**：访问 https://git-scm.com/download/win 下载安装。

**Linux**：`sudo apt install -y git`

**macOS**：`brew install git`

验证：

```powershell
git --version
```

#### 3.1.8 Docker 安装（用于一键启动基础设施）

**Windows / macOS**：安装 Docker Desktop。
**Linux**：安装 Docker Engine + Docker Compose。

验证：

```powershell
docker --version
docker compose version
```

### 3.2 PostgreSQL 扩展配置

如果你使用的是 3.1.5 中的 Docker 镜像方式，扩展已经预装好了，可以跳过本节。如果你是手动安装的 PostgreSQL，需要按以下步骤配置。

#### 3.2.1 连接到 PostgreSQL

```powershell
# Docker 方式
docker exec -it aitown-postgres psql -U ai_town -d ai_town

# 本地安装方式
psql -U postgres
```

#### 3.2.2 创建数据库和用户（如果尚未创建）

```sql
-- 创建用户（密码可自定义，但要和 .env 中的 DATABASE_URL 一致）
CREATE USER ai_town WITH PASSWORD 'password';

-- 创建数据库（归属 ai_town 用户）
CREATE DATABASE ai_town OWNER ai_town;

-- 授权
GRANT ALL PRIVILEGES ON DATABASE ai_town TO ai_town;

-- 切换到 ai_town 数据库
\c ai_town
```

#### 3.2.3 安装扩展

```sql
-- pg_uuidv7：时间有序 UUID（主键生成）
CREATE EXTENSION IF NOT EXISTS pg_uuidv7;

-- pgvector：向量检索（记忆系统依赖）
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm：模糊匹配（搜索功能依赖）
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

#### 3.2.4 验证扩展安装

```sql
-- 验证 pg_uuidv7
SELECT uuidv7();
-- 应输出一个 UUID，如：0190a3b8-7e1f-7xxx-xxxx-xxxxxxxxxxxx

-- 验证 pgvector
SELECT '[1,2,3]'::vector;
-- 应输出：[1,2,3]

-- 验证 pg_trgm
SELECT show_trgm('hello');
-- 应输出一个 trigram 数组

-- 查看已安装的扩展
\dx
```

### 3.3 Redis 配置

#### 3.3.1 启动 Redis

```powershell
# Docker 方式（推荐）
docker run -d --name aitown-redis -p 6379:6379 redis:8.0-alpine

# 或使用项目的 docker-compose
docker compose -f docker-compose.infra.yml up -d redis
```

#### 3.3.2 验证连接

```powershell
redis-cli ping
# 输出：PONG

# 查看数据库信息
redis-cli info server | findstr redis_version
# 输出：redis_version:8.0.x
```

### 3.4 项目克隆与目录结构

#### 3.4.1 克隆项目

```powershell
cd e:\projects
git clone <项目仓库地址> aitown
cd aitown
```

> 如果你已经把项目放在 `e:\projects\aitown`，跳过此步。

#### 3.4.2 目录结构详解

```
aitown/
├── packages/                    # 所有可运行的代码包
│   ├── backend/                 # Python 后端（FastAPI + LangChain）
│   │   ├── src/
│   │   │   ├── core/            # 核心引擎
│   │   │   │   ├── world_engine.py       # World Tick 主循环
│   │   │   │   ├── character_tick.py     # Character Tick 五阶段闭环
│   │   │   │   └── evolutions/           # 世界演化规则（时间/天气/资源/场景/事件）
│   │   │   ├── actions/         # Action 系统
│   │   │   │   ├── base.py              # Action 数据结构定义
│   │   │   │   ├── registry.py          # Action 注册表（候选过滤）
│   │   │   │   ├── move.py              # 移动类 Action
│   │   │   │   ├── life.py              # 生活类 Action（睡觉/吃饭/弹琴）
│   │   │   │   ├── work.py              # 工作类 Action
│   │   │   │   └── social.py            # 社交类 Action
│   │   │   ├── adapters/        # 外部平台适配器
│   │   │   │   ├── onebot.py            # QQ 机器人（OneBot v11/v12）
│   │   │   │   └── lark.py              # 飞书
│   │   │   ├── auth/            # 鉴权（JWT + API Key）
│   │   │   ├── cost_control/    # 成本控制（预算 + 熔断器）
│   │   │   ├── db/              # 数据访问层
│   │   │   │   ├── models/              # SQLAlchemy 模型
│   │   │   │   ├── repositories/        # Repository 模式
│   │   │   │   ├── session.py           # 数据库会话管理
│   │   │   │   └── base.py              # Declarative Base
│   │   │   ├── llm/             # LLM 客户端
│   │   │   │   ├── client.py            # LangChain 封装
│   │   │   │   └── prompts.py           # Prompt 模板
│   │   │   ├── memory/          # 记忆系统
│   │   │   │   ├── episode_service.py   # 记忆写入
│   │   │   │   ├── retrieval_service.py # 记忆检索（pgvector）
│   │   │   │   ├── reflection_service.py# 反思生成
│   │   │   │   └── embedding_worker.py  # 异步向量化 Worker
│   │   │   ├── messaging/       # 消息服务
│   │   │   │   ├── service.py           # 消息处理（用户 ↔ 角色）
│   │   │   │   ├── websocket.py         # WebSocket 实时通信
│   │   │   │   └── proactive_sharing.py # 主动分享
│   │   │   ├── modules/         # Phase 2 模块
│   │   │   │   ├── character/           # 角色卡导入
│   │   │   │   ├── town/                # 小镇场景加载
│   │   │   │   ├── movement/            # 移动系统（Dijkstra 路径规划）
│   │   │   │   ├── schedule/            # 作息系统
│   │   │   │   ├── duration/            # 动态耗时计算
│   │   │   │   └── relation/            # 关系图谱
│   │   │   ├── observability/   # 可观测性
│   │   │   │   ├── tracing.py           # OpenTelemetry
│   │   │   │   ├── metrics.py           # Prometheus
│   │   │   │   ├── logging.py           # structlog
│   │   │   │   └── langfuse_tracing.py  # Langfuse LLM 追踪
│   │   │   ├── scheduler/       # 定时任务（分区预创建）
│   │   │   ├── security/        # 安全（速率限制、Prompt 注入防护）
│   │   │   ├── config.py        # 配置（环境变量加载）
│   │   │   └── main.py          # FastAPI 入口
│   │   ├── alembic/             # 数据库迁移脚本
│   │   │   ├── versions/                # 迁移版本（0001_init 到 0006）
│   │   │   └── env.py                   # Alembic 环境
│   │   ├── tests/               # 单元测试
│   │   ├── pyproject.toml       # Python 依赖声明
│   │   └── alembic.ini          # Alembic 配置
│   ├── frontend/                # React 19 前端
│   │   ├── src/
│   │   │   ├── routes/          # 页面（TanStack Router 文件路由）
│   │   │   │   ├── index.tsx            # 首页
│   │   │   │   ├── characters.tsx       # 角色列表
│   │   │   │   ├── characters.$characterId.tsx  # 角色详情 + 聊天
│   │   │   │   ├── world.tsx            # 世界状态
│   │   │   │   ├── map.tsx              # 小镇地图
│   │   │   │   ├── admin.tsx            # 管理后台
│   │   │   │   └── login.tsx            # 登录
│   │   │   ├── components/      # 通用组件
│   │   │   ├── hooks/           # 自定义 Hook
│   │   │   ├── lib/             # API 客户端、Query 配置
│   │   │   └── stores/          # Zustand 状态
│   │   ├── package.json
│   │   └── vite.config.ts
│   └── shared/                  # 前后端共享（types / openapi）
├── configs/                     # 配置文件
│   ├── characters/              # 角色卡 YAML（kanade/koharu/rin/yuina）
│   ├── prompts/                 # Prompt 模板（chat/decision/reflection）
│   ├── scenes.yaml              # 小镇场景定义
│   ├── world-map.yaml           # 小镇地图（场景连接关系）
│   └── events.yaml              # 事件配置
├── docker/                      # Docker 相关
│   ├── postgres/Dockerfile      # PG 17 + pg_uuidv7 + pgvector
│   └── observability/           # Prometheus / Loki / Grafana 配置
├── docs/                        # 所有设计文档
├── .env.example                 # 环境变量模板
├── docker-compose.infra.yml     # 基础设施（PG/Redis/监控）
├── docker-compose-win.infra.yml # Windows 版基础设施
└── README.md
```

---

## 四、后端启动详解

### 4.1 依赖安装

#### 4.1.1 进入后端目录

```powershell
cd e:\projects\aitown\packages\backend
```

#### 4.1.2 执行 uv sync

```powershell
uv sync
```

**这条命令做了什么**：

1. 检测 `pyproject.toml` 中的 Python 版本要求（`>=3.13`）
2. 如果系统没有 3.13，uv 会自动下载对应版本的 Python
3. 创建一个虚拟环境（`.venv/` 目录）
4. 解析 `pyproject.toml` 中的所有依赖（FastAPI、SQLAlchemy、LangChain……）
5. 下载并安装所有依赖到虚拟环境
6. 生成/更新 `uv.lock` 锁文件（确保依赖版本可复现）

**首次执行大约需要 2–5 分钟**（取决于网络速度）。

#### 4.1.3 常见错误处理

**错误 1：Python 版本不匹配**

```
error: Requested Python version (>=3.13) not found
```

解决：uv 会自动下载，如果网络问题，可以手动安装 Python 3.13 后重试。

**错误 2：网络超时**

```
error: Failed to download package
```

解决：配置国内镜像源。在 `pyproject.toml` 同级目录创建 `uv.toml`：

```toml
[[index]]
url = "https://pypi.tuna.tsinghua.edu.cn/simple"
default = true
```

**错误 3：编译失败（asyncpg 等）**
解决：Windows 需要安装 Visual Studio Build Tools；Linux 需要 `python3.13-dev`。

### 4.2 环境变量配置

#### 4.2.1 创建 .env 文件

```powershell
# 在 packages/backend 目录下
Copy-Item ../../.env.example .env
```

#### 4.2.2 必填变量详细说明

打开 `.env` 文件，按以下说明修改：

**数据库连接**：

```env
# 格式：postgresql+asyncpg://用户名:密码@主机:端口/数据库名
DATABASE_URL=postgresql+asyncpg://ai_town:password@localhost:5432/ai_town
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
```

如果你用 Docker 启动的 PG，用户名是 `ai_town`，密码是 `password`，端口 `5432`，数据库名 `ai_town`，直接用上面的配置即可。

**Redis 连接**：

```env
# 格式：redis://主机:端口/数据库号
REDIS_URL=redis://localhost:6379/0
```

#### 4.2.3 LLM API Key 配置

项目使用 OpenAI 兼容协议，支持任何兼容的 LLM 提供商：

```env
# OpenAI 官方
OPENAI_API_KEY=sk-你的真实key
OPENAI_BASE_URL=https://api.openai.com/v1

# 或：DeepSeek
OPENAI_API_KEY=sk-你的deepseek-key
OPENAI_BASE_URL=https://api.deepseek.com/v1

# 或：智谱 GLM
OPENAI_API_KEY=你的智谱key
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# 或：Moonshot Kimi
OPENAI_API_KEY=sk-你的kimi-key
OPENAI_BASE_URL=https://api.moonshot.cn/v1

# 或：本地 Ollama
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
```

**模型配置**（按你的提供商修改）：

```env
MODEL_CHAT=gpt-4o-mini        # 日常对话模型（便宜）
MODEL_STRONG=gpt-4o           # 复杂决策模型（Character Tick 用）
MODEL_FLASH=gpt-3.5-turbo     # 快速任务模型（分类、判断）
```

**Embedding 配置**：

```env
EMBEDDING_DIM=1536
EMBEDDING_MODEL=text-embedding-3-small
```

> ⚠️ **重要**：`EMBEDDING_DIM` 必须和你的 embedding 模型一致！
>
> - OpenAI text-embedding-3-small → 1536
> - OpenAI text-embedding-3-large → 3072
> - BGE-m3 → 1024
>
> 如果修改了维度，需要重建数据库（`alembic downgrade base && alembic upgrade head`）。

#### 4.2.4 鉴权配置

```env
# JWT 密钥（生产环境务必修改为随机长字符串）
JWT_SECRET=your-super-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=24

# API Key（用于 API 调用鉴权，可选）
API_KEY=your-api-key

# 管理员账号（用于登录 Web 后台）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
```

生成一个安全的 JWT_SECRET：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

#### 4.2.5 QQ OneBot 配置（可选）

```env
# 默认对话角色 ID（启动后创建角色，把角色 UUID 填这里）
ONEBOT_DEFAULT_CHARACTER_ID=

# 机器人自身的 QQ 号
ONEBOT_SELF_ID=

# 群聊是否仅在被 @ 时回复（false=智能回复模式）
ONEBOT_GROUP_AT_ONLY=false

# 群-角色映射：JSON 字符串
ONEBOT_GROUP_CHARACTER_MAP={}
```

### 4.3 数据库迁移

#### 4.3.1 执行迁移

```powershell
# 确保在 packages/backend 目录，且 .env 已配置好 DATABASE_URL
alembic upgrade head
```

#### 4.3.2 迁移脚本做了什么

迁移脚本位于 `alembic/versions/` 目录，按版本号顺序执行：

| 版本                      | 内容                                                                                                                                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0001_init`               | 创建扩展（vector/pg_trgm）+ 核心表（characters/character_states/action_records 分区表/memory_episodes 含向量/HNSW 索引/plans/reflections/world_events/world_snapshots） |
| `0002_optimize`           | 性能优化索引                                                                                                                                                            |
| `0003_messages`           | 消息表（conversations/messages）                                                                                                                                        |
| `0004_phase3_refinements` | Phase 3 字段调整                                                                                                                                                        |
| `0005_embedding_dim_2048` | 调整 embedding 维度                                                                                                                                                     |
| `0006_world_event_key`    | world_events 主键调整                                                                                                                                                   |

**核心表说明**：

- `characters`：角色档案（name/age/occupation/personality/traits/backstory）
- `character_states`：角色实时状态（location/stamina/satiety/mood/money）
- `action_records`：行为历史（按月分区，存储每次 Action 执行记录）
- `memory_episodes`：记忆流（含 1536 维向量，HNSW 索引）
- `plans`：角色计划
- `reflections`：反思记录
- `world_events`：世界事件（差分记录）
- `world_snapshots`：世界快照（冷启动恢复）
- `conversations`：会话
- `messages`：消息历史（按月分区）

#### 4.3.3 查看迁移状态

```powershell
# 查看当前迁移版本
alembic current

# 查看迁移历史
alembic history --verbose

# 回滚一个版本
alembic downgrade -1

# 回滚到初始状态（慎用！会删除所有数据）
alembic downgrade base
```

#### 4.3.4 常见迁移错误处理

**错误 1：扩展不存在**

```
sqlalchemy.exc.ProgrammingError: function uuidv7() does not exist
```

解决：按 3.2 节安装 pg_uuidv7 扩展。

**错误 2：连接失败**

```
sqlalchemy.exc.OperationalError: connection refused
```

解决：检查 PostgreSQL 是否启动、`DATABASE_URL` 是否正确。

**错误 3：权限不足**

```
permission denied for relation characters
```

解决：`GRANT ALL ON ALL TABLES IN SCHEMA public TO ai_town;`

**错误 4：向量维度不匹配**

```
vector dimension does not match
```

解决：确保 `.env` 中的 `EMBEDDING_DIM` 与迁移脚本一致（默认 1536）。如需修改维度，执行 `alembic downgrade base` 后修改 `EMBEDDING_DIM` 再 `alembic upgrade head`。

### 4.4 启动后端

#### 4.4.1 启动命令

```powershell
# 确保在 packages/backend 目录
uvicorn src.main:app --reload --port 8000
```

**参数说明**：

- `src.main:app`：FastAPI 应用实例的位置（`src/main.py` 文件中的 `app` 变量）
- `--reload`：代码修改后自动重启（开发模式）
- `--port 8000`：监听 8000 端口

#### 4.4.2 启动后应该看到的日志

启动成功后，控制台会输出类似以下日志（JSON 格式）：

```json
{"event":"ai_town_backend_starting","logger":"src.main","level":"info"}
{"event":"logging_configured","format":"json","level":"info","logger":"src.main"}
{"event":"redis_connected","url":"redis://localhost:6379/0","logger":"src.main","level":"info"}
{"event":"cost_control_initialized","daily_budget":10.0,"circuit_threshold":5,"logger":"src.main","level":"info"}
{"event":"partitions_pre_created","months_ahead":3,"logger":"src.main","level":"info"}
{"event":"llm_initialized","model":"gpt-4o-mini","logger":"src.main","level":"info"}
{"event":"action_registry_initialized","count":15,"logger":"src.main","level":"info"}
{"event":"embedding_worker_started","batch_size":20,"poll_interval":5.0,"logger":"src.main","level":"info"}
{"event":"partition_scheduler_started","logger":"src.main","level":"info"}
{"event":"world_engine_started","logger":"src.main","level":"info"}
{"event":"character_engine_started","logger":"src.main","level":"info"}
{"event":"phase2_modules_initialized","logger":"src.main","level":"info"}
{"event":"ws_manager_ready","endpoint":"/ws/chat/{character_id}","logger":"src.main","level":"info"}
{"event":"onebot_adapter_started","endpoint":"/ws/onebot/v12","logger":"src.main","level":"info"}
{"event":"observability_initialized","logger":"src.main","level":"info"}
{"event":"world_engine_starting","logger":"src.core.world_engine","level":"info"}
{"event":"character_tick_loop_started","interval":30,"logger":"src.main","level":"info"}
```

**关键日志解读**：

- `redis_connected`：Redis 连接成功
- `llm_initialized`：LLM 客户端初始化成功
- `action_registry_initialized`：Action 注册完成（count 表示注册了多少个 Action）
- `world_engine_started`：世界引擎已启动
- `character_engine_started`：角色 Tick 引擎已启动
- `character_tick_loop_started`：角色 Tick 循环开始运行

#### 4.4.3 验证启动成功

打开浏览器或用 curl 访问健康检查接口：

```powershell
curl http://localhost:8000/health
```

返回：

```json
{
  "status": "ok",
  "world_tick": 5,
  "redis": "connected",
  "character_engine": "available"
}
```

访问 Swagger UI 查看所有 API：

```
http://localhost:8000/docs
```

访问管理状态接口（需先登录获取 token）：

```powershell
# 登录
curl -X POST http://localhost:8000/api/v1/auth/login `
  -H "Content-Type: application/json" `
  -d '{"username":"admin","password":"admin123"}'

# 返回：{"token":"eyJ...","user_id":"admin","expires_in":86400}

# 用 token 查看系统状态
curl http://localhost:8000/api/v1/admin/status `
  -H "Authorization: Bearer eyJ..."
```

#### 4.4.4 常见启动错误处理

**错误 1：Redis 连接失败**

```
redis_connection_failed: Error 111 connecting to localhost:6379
```

解决：启动 Redis（`docker compose -f docker-compose.infra.yml up -d redis`）。

**错误 2：数据库连接失败**

```
sqlalchemy.exc.OperationalError: connection refused
```

解决：启动 PostgreSQL，检查 `.env` 中的 `DATABASE_URL`。

**错误 3：LLM 初始化失败**

```
llm_initialization_failed: AuthenticationError
```

解决：检查 `OPENAI_API_KEY` 是否正确、`OPENAI_BASE_URL` 是否可达。

**错误 4：端口被占用**

```
[Errno 48] address already in use
```

解决：换端口 `uvicorn src.main:app --reload --port 8001`，或杀掉占用进程：

```powershell
# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Linux/Mac
lsof -i :8000
kill -9 <PID>
```

**错误 5：scene_config_not_found 警告**

```
scene_config_not_found: path=e:\projects\aitown\configs\scenes.yaml
```

这是警告不是错误，小镇场景配置文件未找到。检查 `configs/scenes.yaml` 和 `configs/world-map.yaml` 是否存在。

---

## 五、前端启动

### 5.1 依赖安装

```powershell
cd e:\projects\aitown\packages\frontend
pnpm install
```

**这条命令做了什么**：

1. 读取 `package.json` 中的依赖列表
2. 通过 pnpm 的硬链接机制（`pnpm-store`）安装依赖，节省磁盘空间
3. 生成 `node_modules/` 和 `pnpm-lock.yaml`

首次安装大约 1–3 分钟。

### 5.2 启动开发服务器

```powershell
pnpm dev
```

启动后控制台输出：

```
  VITE v8.1.x  ready in 320 ms

  ➜  Local:   http://localhost:5173/
  ➜  Network: use --host to expose
```

### 5.3 访问前端

打开浏览器访问：**http://localhost:5173**

### 5.4 前端功能概览

前端基于 React 19 + TanStack Router，提供以下页面：

| 路由              | 功能                                  |
| ----------------- | ------------------------------------- |
| `/`               | 首页（项目介绍与快速入口）            |
| `/login`          | 登录页（用 admin/admin123 登录）      |
| `/characters`     | 角色列表（查看所有角色）              |
| `/characters/:id` | 角色详情 + 聊天界面（与角色实时对话） |
| `/world`          | 世界状态（当前时间、天气、Tick 数）   |
| `/map`            | 小镇地图（查看角色分布与场景）        |
| `/admin`          | 管理后台（强制 Tick、模块状态等）     |

**首次使用流程**：

1. 访问 `/login`，用 admin/admin123 登录
2. 访问 `/characters` 查看角色列表（如果还没有角色，参考第七章创建）
3. 点击某个角色进入 `/characters/:id`，开始聊天
4. 访问 `/world` 观察世界状态变化
5. 访问 `/admin` 查看系统运行状态

---

## 六、QQ 机器人接入（可选但推荐）

### 6.1 安装 OneBot 实现

本项目支持任何兼容 OneBot v11/v12 协议的 QQ 机器人实现。推荐使用 **NapCat**。

#### 6.1.1 NapCat 安装指南

1. 访问 NapCat 发布页：https://github.com/NapNeko/NapCatQQ/releases
2. 下载对应平台的最新版本
3. 按照官方文档安装并登录 QQ 账号

#### 6.1.2 配置反向 WebSocket 连接

在 NapCat 的配置文件中（通常是 `config/onebot11_<QQ号>.json`），添加反向 WebSocket 连接：

```json
{
  "network": {
    "websocketClients": [
      {
        "enable": true,
        "url": "ws://localhost:8000/ws/onebot/v12",
        "reconnectInterval": 3000
      }
    ]
  }
}
```

**关键点**：

- `url` 必须是 `ws://localhost:8000/ws/onebot/v12`（后端的 WebSocket 端点）
- 后端作为 WebSocket 服务端，NapCat 主动连接后端（反向连接）

### 6.2 配置 OneBot 适配器

编辑 `packages/backend/.env` 文件：

```env
# 默认对话角色 ID
# 启动后端 → 创建角色 → 把角色 UUID 填到这里 → 重启后端
ONEBOT_DEFAULT_CHARACTER_ID=0190a3b8-7e1f-7xxx-xxxx-xxxxxxxxxxxx

# 机器人自身的 QQ 号（用于群聊 @ 检测）
ONEBOT_SELF_ID=123456789

# 群聊是否仅在被 @ 时回复
# false = 智能回复模式（读取所有群消息，LLM 判断是否回复）
# true  = 仅 @ 回复模式
ONEBOT_GROUP_AT_ONLY=false

# 群-角色映射：不同群绑定不同角色
# 格式：{"群号": "角色UUID"}
ONEBOT_GROUP_CHARACTER_MAP={"987654321": "0190a3b8-7e1f-7xxx-xxxx-xxxxxxxxxxxx"}
```

### 6.3 验证 QQ 连接

#### 6.3.1 查看连接日志

启动后端后，当 NapCat 连接成功时，控制台会输出：

```json
{ "event": "onebot_client_connected", "logger": "src.adapters.onebot", "level": "info" }
```

如果看到这个日志，说明 QQ 机器人已经成功连接到后端。

#### 6.3.2 发送私聊消息测试

1. 用你的 QQ 加机器人为好友
2. 私聊机器人发一条消息，如"你好"
3. 后端会处理消息：
   - 把消息转发给 `ONEBOT_DEFAULT_CHARACTER_ID` 指定的角色
   - 角色生成回复
   - 通过 OneBot 的 `send_private_msg` 发送回去

后端日志会显示：

```json
{"event":"onebot_message_received","message_type":"private","user_id":"你的QQ","logger":"src.adapters.onebot","level":"info"}
{"event":"message_handled","character_id":"...","logger":"src.messaging.service","level":"info"}
```

#### 6.3.3 发送群聊消息测试

1. 把机器人拉进一个群
2. 在群里发消息：
   - 如果 `ONEBOT_GROUP_AT_ONLY=false`，直接发消息，机器人会智能判断是否回复
   - 如果 `ONEBOT_GROUP_AT_ONLY=true`，需要 @ 机器人才会回复
3. 机器人回复会以多条消息形式发送（长回复按段落拆分，模拟真人打字）

**群-角色路由**：

- 如果群号在 `ONEBOT_GROUP_CHARACTER_MAP` 中，使用映射的角色
- 否则使用 `ONEBOT_DEFAULT_CHARACTER_ID` 指定的默认角色

---

## 七、角色创建与配置

### 7.1 通过角色卡 YAML 创建（推荐）

项目使用 YAML 格式的"角色卡"定义角色。这是最简单的创建方式。

#### 7.1.1 角色卡 YAML 格式

参考 `configs/characters/kanade.yaml`：

```yaml
# 基本信息
name: 奏
age: 17
occupation: 高中生

# 性格特征（列表）
personality:
  - 温柔
  - 神秘
  - 喜欢音乐
  - 有灵性

# 其他特征（键值对）
traits:
  hobby: [弹钢琴, 写日记, 观星]
  favorite_color: moonlight_silver
  schedule: early_bird # 作息类型：early_bird / night_owl
  favorite_food: [和果子, 抹茶]
  dislikes: [噪音, 撒谎]
  mbti: INFJ

# 背景故事（多行字符串）
backstory: |
  从小在神社长大，对神灵有感应。
  性格温柔但有些神秘，喜欢在清晨弹钢琴。
  和结衣奈在公园偶遇后成为朋友。

# 头像 URL
avatar_url: https://cdn.example.com/avatar/kanade.png

# 语音预设
voice_preset: gentle_girl

# 初始状态
initial_state:
  location: shrine # 初始场景（必须是 scenes.yaml 中定义的）
  stamina: 75 # 体力 0-100
  satiety: 55 # 饱腹度 0-100
  mood: calm # 情绪：happy/calm/sad/angry/excited/tired
  money: 300 # 金钱
  phone_battery: 50 # 手机电量 0-100
  social_energy: 50 # 社交能量 0-100

# 初始计划
initial_plans:
  - type: long_term # 长期计划
    title: 完成一首钢琴曲
    priority: 4 # 1-5
  - type: short_term # 短期计划
    title: 在祭典上演奏
    priority: 5
```

#### 7.1.2 单个角色导入

```powershell
# 登录获取 token
$loginResp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/login" `
  -Method Post -ContentType "application/json" `
  -Body '{"username":"admin","password":"admin123"}'
$token = $loginResp.token

# 导入角色卡
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/admin/characters/import" `
  -Method Post `
  -Headers @{Authorization = "Bearer $token"} `
  -Form "yaml_file=@configs/characters/kanade.yaml"
```

返回：

```json
{
  "message": "角色导入成功",
  "character": {
    "id": "0190a3b8-7e1f-7xxx-xxxx-xxxxxxxxxxxx",
    "name": "奏",
    "age": 17,
    "occupation": "高中生"
  }
}
```

#### 7.1.3 批量导入角色

项目已经预置了 4 个角色卡（kanade/koharu/rin/yuina），可以一键全部导入：

```powershell
# 登录获取 token
$loginResp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/login" `
  -Method Post -ContentType "application/json" `
  -Body '{"username":"admin","password":"admin123"}'
$token = $loginResp.token

# 批量导入
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/admin/characters/import-batch?directory=configs/characters" `
  -Method Post `
  -Headers @{Authorization = "Bearer $token"}
```

返回：

```json
{
  "message": "批量导入完成: 4 个角色",
  "characters": [
    { "id": "...", "name": "奏" },
    { "id": "...", "name": "小春" },
    { "id": "...", "name": "凛" },
    { "id": "...", "name": "结衣奈" }
  ],
  "total": 4
}
```

### 7.2 角色状态字段说明

角色状态分两层：

**PostgreSQL `character_states` 表**（持久态镜像）：

| 字段             | 类型        | 默认值 | 说明               |
| ---------------- | ----------- | ------ | ------------------ |
| `character_id`   | UUID        | —      | 角色 ID（外键）    |
| `location`       | String      | "home" | 当前场景 ID        |
| `stamina`        | Int         | 80     | 体力（0-100）      |
| `satiety`        | Int         | 60     | 饱腹度（0-100）    |
| `mood`           | String      | "calm" | 情绪状态           |
| `money`          | Int         | 500    | 金钱               |
| `phone_battery`  | Int         | 75     | 手机电量（0-100）  |
| `social_energy`  | Int         | 60     | 社交能量（0-100）  |
| `inventory`      | JSONB       | {}     | 背包物品           |
| `current_action` | JSONB       | null   | 当前正在执行的动作 |
| `updated_at`     | TIMESTAMPTZ | now()  | 最后更新时间       |

**Redis `char:{character_id}:state` 哈希表**（实时态，主）：

- 字段同上，但以 Redis 哈希表形式存储
- Character Tick 优先读写 Redis，PG 是镜像备份

**情绪枚举**：`happy` / `calm` / `sad` / `angry` / `excited` / `tired` / `anxious`

**场景 ID 参考**（来自 `configs/scenes.yaml`）：

- `home` 家
- `school` 学校
- `cafe` 咖啡店
- `bookstore` 书店
- `library` 图书馆
- `park` 公园
- `shrine` 神社

### 7.3 查看角色

#### 7.3.1 获取角色列表

```powershell
# 获取所有角色
curl http://localhost:8000/api/v1/characters `
  -H "Authorization: Bearer $token"

# 只获取活跃角色（is_active=true）
curl "http://localhost:8000/api/v1/characters?active_only=true" `
  -H "Authorization: Bearer $token"
```

返回：

```json
{
  "data": [
    {
      "id": "0190a3b8-...",
      "name": "奏",
      "age": 17,
      "occupation": "高中生",
      "is_active": true
    }
  ],
  "total": 1
}
```

#### 7.3.2 获取角色详情（含实时状态）

```powershell
curl http://localhost:8000/api/v1/characters/0190a3b8-... `
  -H "Authorization: Bearer $token"
```

返回：

```json
{
  "character": {
    "id": "0190a3b8-...",
    "name": "奏",
    "age": 17,
    "occupation": "高中生",
    "personality": ["温柔", "神秘", "喜欢音乐", "有灵性"],
    "traits": {
      "hobby": ["弹钢琴", "写日记", "观星"],
      "schedule": "early_bird"
    },
    "backstory": "从小在神社长大...",
    "is_active": true
  },
  "state": {
    "location": "shrine",
    "stamina": 75,
    "satiety": 55,
    "mood": "calm",
    "money": 300
  }
}
```

---

## 八、与世界交互

### 8.1 查看世界状态

#### 8.1.1 获取当前世界状态

```powershell
curl http://localhost:8000/api/v1/world `
  -H "Authorization: Bearer $token"
```

返回：

```json
{
  "tick_id": 128,
  "world_time": "08:30",
  "weather": "sunny",
  "temperature": 22,
  "active_characters": 4
}
```

#### 8.1.2 World Tick 运行机制

- **Tick 频率**：每 30 秒一次（`WORLD_TICK_SECONDS=30`）
- **时间推进**：每次 Tick 推进 10 分钟（`WORLD_TICK_MINUTES=10`）
- **天气变化**：每 60 个 Tick 变一次（约 30 分钟真实时间）
- **快照持久化**：每 120 个 Tick 写一次差分事件到 `world_events` 表
- **完整快照**：每 1000 个 Tick 写一次完整快照到 `world_snapshots` 表（冷启动恢复用）

**手动触发 World Tick**（调试用）：

```powershell
curl -X POST http://localhost:8000/api/v1/admin/world/tick `
  -H "Authorization: Bearer $token"
```

#### 8.1.3 查看指定 Tick 的世界事件

```powershell
curl http://localhost:8000/api/v1/world/events/128 `
  -H "Authorization: Bearer $token"
```

### 8.2 与角色对话

#### 8.2.1 通过 REST API 发送消息

```powershell
curl -X POST "http://localhost:8000/api/v1/messages/send?character_id=0190a3b8-...&user_id=user1&platform=web&content=你好呀" `
  -H "Authorization: Bearer $token"
```

返回：

```json
{
  "data": {
    "conversation_id": "0190a3b9-...",
    "message_id": "0190a3ba-...",
    "content": "你好~今天天气真好，我刚才在神社弹了一首曲子，心情很平静。",
    "tokens": { "prompt": 320, "completion": 45, "total": 365 },
    "cost": 0.0003,
    "error": null
  }
}
```

#### 8.2.2 通过 WebSocket 实时对话

前端使用 WebSocket 与角色实时对话，端点：`ws://localhost:8000/ws/chat/{character_id}`

JavaScript 示例：

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/chat/0190a3b8-...");

ws.onopen = () => {
  ws.send(
    JSON.stringify({
      type: "message",
      content: "你今天做了什么？",
      user_id: "user1",
    }),
  );
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log("角色回复：", data.content);
};
```

### 8.3 观察角色行为

#### 8.3.1 角色自主行动

角色会通过 Character Tick 自主决策并执行 Action。你可以观察：

```powershell
# 强制对单个角色触发 Tick（调试用）
curl -X POST "http://localhost:8000/api/v1/admin/tick?character_id=0190a3b8-..." `
  -H "Authorization: Bearer $token"

# 强制对所有活跃角色触发 Tick
curl -X POST "http://localhost:8000/api/v1/admin/tick" `
  -H "Authorization: Bearer $token"
```

#### 8.3.2 查看 Action 记录

```powershell
# 获取角色行为历史
curl "http://localhost:8000/api/v1/characters/0190a3b8-.../actions?limit=10" `
  -H "Authorization: Bearer $token"
```

返回：

```json
{
  "data": [
    {
      "id": "0190a3bb-...",
      "action_id": "play_piano",
      "action_name": "弹钢琴",
      "params": { "piece": "月光奏鸣曲" },
      "reason": "清晨适合练琴，而且今天心情很好",
      "result": "弹奏了一段月光奏鸣曲，心情变得平静",
      "duration_minutes": 30,
      "location": "shrine",
      "related_characters": [],
      "timestamp": "2026-07-11T08:30:00+08:00"
    }
  ],
  "total": 1
}
```

#### 8.3.3 查看记忆片段

```powershell
curl "http://localhost:8000/api/v1/memories/0190a3b8-...?limit=5" `
  -H "Authorization: Bearer $token"
```

返回：

```json
{
  "data": [
    {
      "id": "0190a3bc-...",
      "content": "清晨在神社弹钢琴，弹奏了月光奏鸣曲，心情很平静",
      "timestamp": "2026-07-11T08:30:00+08:00",
      "importance": 7,
      "is_reflected": false
    }
  ],
  "total": 1
}
```

#### 8.3.4 查看反思与计划

```powershell
# 查看反思
curl "http://localhost:8000/api/v1/characters/0190a3b8-.../reflections" `
  -H "Authorization: Bearer $token"

# 查看计划
curl "http://localhost:8000/api/v1/characters/0190a3b8-.../plans" `
  -H "Authorization: Bearer $token"
```

---

## 九、可观测性配置（可选）

### 9.1 Prometheus + Grafana

#### 9.1.1 启动监控栈

项目已经预置了完整的可观测性栈，一键启动：

```powershell
docker compose -f docker-compose.infra.yml up -d prometheus loki jaeger alloy grafana
```

这会启动：

- **Prometheus**（端口 9090）：指标采集
- **Loki**（端口 3100）：日志聚合
- **Jaeger**（端口 16686）：链路追踪
- **Alloy**（端口 12345）：日志采集器
- **Grafana**（端口 3000）：统一可视化面板

#### 9.1.2 访问 Grafana

1. 打开浏览器访问：http://localhost:3000
2. 默认账号：`admin` / `admin123`
3. 项目已预置 3 个 Dashboard：
   - **AI Town Overview**：系统总览（World Tick、角色数、Redis 状态）
   - **Character Tick**：角色 Tick 性能（耗时、错误率、并发数）
   - **LLM**：LLM 调用（Token、Cost、延迟）

#### 9.1.3 关键指标说明

| 指标                          | 说明                              |
| ----------------------------- | --------------------------------- |
| `world_tick_total`            | World Tick 累计次数               |
| `world_tick_id`               | 当前 Tick ID                      |
| `world_tick_duration_seconds` | World Tick 耗时                   |
| `world_tick_errors_total`     | World Tick 错误次数               |
| `active_characters`           | 活跃角色数                        |
| `character_tick_errors_total` | Character Tick 错误次数（按角色） |
| `redis_connected`             | Redis 连接状态（1=连接，0=断开）  |

### 9.2 日志查看

#### 9.2.1 日志格式

后端使用 `structlog` 输出 JSON 格式日志，便于聚合查询。每条日志包含：

- `event`：事件名（如 `world_tick_complete`）
- `level`：日志级别
- `logger`：模块名
- 其他上下文字段（如 `tick_id`、`character_id`）

#### 9.2.2 关键日志事件

| 事件                          | 说明               |
| ----------------------------- | ------------------ |
| `world_engine_starting`       | 世界引擎启动       |
| `world_tick_complete`         | World Tick 完成    |
| `character_tick_batch_start`  | 角色 Tick 批次开始 |
| `character_tick_failed`       | 角色 Tick 失败     |
| `character_tick_rate_limited` | 角色 Tick 遇到限流 |
| `action_executed`             | Action 执行完成    |
| `memory_episode_created`      | 记忆创建           |
| `reflection_generated`        | 反思生成           |
| `onebot_message_received`     | 收到 QQ 消息       |
| `llm_call_complete`           | LLM 调用完成       |

#### 9.2.3 在 Grafana 中查询日志

1. 打开 Grafana → Explore → 选择 Loki 数据源
2. 查询示例：
   - `{app="ai-town-backend"} |= "error"`：查看所有错误日志
   - `{app="ai-town-backend"} |= "character_tick_failed"`：查看角色 Tick 失败
   - `{app="ai-town-backend"} |= "world_tick"`：查看 World Tick 相关日志

---

## 十、常见问题排查（FAQ）

### Q1：后端启动失败怎么办？

**排查步骤**：

1. 检查 Redis 是否启动：`redis-cli ping`
2. 检查 PostgreSQL 是否启动：`psql -U ai_town -d ai_town -c "SELECT 1"`
3. 检查 `.env` 文件是否存在且配置正确
4. 检查数据库迁移是否执行：`alembic current`
5. 查看后端启动日志中的 `error` 或 `warning` 字段

### Q2：数据库连接失败？

**常见原因**：

- PostgreSQL 未启动
- `DATABASE_URL` 中的用户名/密码/端口/数据库名错误
- PostgreSQL 不允许本地连接（检查 `pg_hba.conf`）

**验证命令**：

```powershell
# 用和 .env 相同的连接串测试
psql "postgresql://ai_town:password@localhost:5432/ai_town" -c "SELECT version();"
```

### Q3：LLM API 调用失败？

**常见原因**：

- `OPENAI_API_KEY` 错误或过期
- `OPENAI_BASE_URL` 不可达
- 余额不足
- 模型名错误（如把 `gpt-4o-mini` 写成了 `gpt-4o_min`）

**验证命令**：

```powershell
curl https://api.openai.com/v1/models `
  -H "Authorization: Bearer sk-你的key"
```

### Q4：QQ 机器人不回复？

**排查步骤**：

1. 检查后端日志是否有 `onebot_client_connected`（NapCat 是否连上）
2. 检查 `ONEBOT_DEFAULT_CHARACTER_ID` 是否配置了有效的角色 UUID
3. 检查角色是否 `is_active=true`
4. 发消息后查看后端日志是否有 `onebot_message_received`
5. 如果群聊不回复，检查 `ONEBOT_GROUP_AT_ONLY` 配置和是否正确 @ 了机器人

### Q5：World Tick 不执行？

**排查步骤**：

1. 检查 `/api/v1/admin/status` 中 `world_engine.running` 是否为 true
2. 检查 `world_engine.is_leader` 是否为 true（多实例时只有一个 leader）
3. 查看日志中是否有 `world_engine_starting` 和 `world_tick_complete`
4. 手动触发：`POST /api/v1/admin/world/tick`

### Q6：Character Tick 报错？

**常见错误**：

- `character_tick_rate_limited`：LLM 限流，系统会自动退避，无需处理
- `character_tick_failed`：查看日志中的 `error` 字段
- `character_engine_not_available`：`CharacterTickEngine` 模块未加载，检查导入错误

**调试技巧**：

```powershell
# 强制对单个角色触发 Tick，查看详细错误
curl -X POST "http://localhost:8000/api/v1/admin/tick?character_id=你的角色UUID" `
  -H "Authorization: Bearer $token"
```

### Q7：端口被占用？

```powershell
# Windows：查看占用端口的进程
netstat -ano | findstr :8000
# 杀掉进程
taskkill /PID <PID> /F

# Linux/Mac
lsof -i :8000
kill -9 <PID>

# 或换端口启动
uvicorn src.main:app --reload --port 8001
```

### Q8：依赖安装失败？

**uv sync 失败**：

- 网络问题：配置国内镜像源（见 4.1.3）
- Python 版本：确保 Python 3.13+，uv 会自动下载
- 编译失败：Windows 安装 VS Build Tools，Linux 安装 `python3.13-dev`

**pnpm install 失败**：

- 网络问题：`pnpm config set registry https://registry.npmmirror.com`
- Node 版本：确保 Node 22+

---

## 十一、进阶学习路径

### 11.1 建议的阅读顺序

项目 `docs/` 目录下有完整的设计文档，建议按以下顺序阅读：

1. **[architecture.md](architecture.md)**：总体架构（分层、数据流、技术栈）— 你已经看过本文档后再读这个会更深入
2. **[character-design.md](character-design.md)**：角色设计（档案、状态、记忆、计划、关系、角色卡）
3. **[town-design.md](town-design.md)**：小镇设计（地图、场景、移动矩阵、资源、节日）
4. **[world-engine.md](world-engine.md)**：世界引擎（World Tick、Character Tick、演化列表、作息、动态耗时）
5. **[action-system.md](action-system.md)**：Action 系统（定义、决策、参数化、主动分享、LLM 边界）
6. **[memory-system.md](memory-system.md)**：记忆系统（三层记忆、pgvector、反思、规划）
7. **[module-system.md](module-system.md)**：模块管理器与本地工具系统
8. **[messaging-service.md](messaging-service.md)**：消息服务（多平台接入、主动推送）
9. **[data-model.md](data-model.md)**：数据模型（全部 DDL、ER 图、索引策略）
10. **[api-spec.md](api-spec.md)**：API 设计（REST、WebSocket、请求/响应示例）
11. **[config-reference.md](config-reference.md)**：配置参考
12. **[frontend-design.md](frontend-design.md)**：前端设计
13. **[observability.md](observability.md)**：可观测性
14. **[deployment.md](deployment.md)**：部署与运维
15. **[docker-deployment.md](docker-deployment.md)**：Docker 部署指南（完整编排、多阶段构建、Profile 按需启动）
16. **[development-guide.md](development-guide.md)**：开发指南
17. **[gap-analysis.md](gap-analysis.md)**：项目不足审查与改进路线图
18. **[roadmap.md](roadmap.md)**：开发路线图

### 11.2 如何修改角色行为

**方式 1：修改角色卡 YAML**

编辑 `configs/characters/kanade.yaml`，修改 `personality`、`backstory`、`initial_plans` 等字段，然后重新导入。注意：重新导入会创建新角色，不会覆盖已有角色。

**方式 2：修改 Prompt 模板**

编辑 `configs/prompts/decision.yaml`（角色决策 Prompt）或 `configs/prompts/chat.yaml`（对话 Prompt），调整 LLM 的决策倾向。

**方式 3：修改 Action 定义**

编辑 `packages/backend/src/actions/life.py`，添加新的 Action 或修改现有 Action 的 `precondition`、`energy_cost` 等。例如添加一个新的"画画"Action：

```python
# 在 src/actions/life.py 中添加
draw_painting = Action(
    id="draw_painting",
    name="画画",
    category=ActionCategory.LIFE,
    scene="home",                    # 只能在家画
    duration_minutes=45,
    energy_cost=-15,                 # 消耗体力
    satiety_cost=-5,                 # 消耗饱腹度
    social_cost=10,                  # 恢复社交能量（放松）
    precondition=lambda s: s.get("stamina", 0) >= 20,
)
registry.register(draw_painting)
```

### 11.3 如何添加新的 Action

1. 在 `packages/backend/src/actions/` 下选择合适的文件（life/work/social/move）
2. 定义 Action 实例，设置 `id`、`name`、`category`、`scene`、`precondition`、`energy_cost` 等
3. 在 `packages/backend/src/actions/__init__.py` 的 `register_all()` 中注册
4. 重启后端，通过 `GET /api/v1/actions` 验证新 Action 是否出现

### 11.4 如何添加新的 Evolution

Evolution 是 World Tick 中的"世界演化规则"。要添加新的演化（如"季节变化"）：

1. 在 `packages/backend/src/core/evolutions/` 下创建 `season_evolution.py`
2. 继承 `base.py` 中的 `BaseEvolution` 类
3. 实现 `evolve(state) -> dict` 方法，返回需要变更的状态字段
4. 在 `evolutions/__init__.py` 的 `default_evolutions()` 中注册

### 11.5 如何开发本地工具

工具已内联到后端进程（`src/tools/`），以进程内 async 函数形式提供，无需独立 Server。开发一个新的本地工具：

1. 在 `packages/backend/src/tools/` 下选择或新建命名空间文件，如 `my_tool.py`
2. 定义 async 函数与工具元数据，注册到 `TOOL_REGISTRY`：

   ```python
   # src/tools/my_tool.py
   from src.tools.registry import TOOL_REGISTRY

   async def my_action(arg: str) -> dict:
       return {"success": True, "result": f"processed {arg}"}

   TOOL_REGISTRY["my_tool.my_action"] = {
       "func": my_action,
       "description": "示例工具",
       "llm_params": {"arg": "输入字符串"},
       "injected_params": {},
       "state_mutating": False,
   }
   ```

3. （可选）在 `src/api/tools.py` 的 `_NAMESPACES` 中登记新命名空间，使其出现在管理 API
4. 重启后端，通过 `GET /api/v1/tools/tools` 验证

详见 [开发指南 - 新增本地工具](development-guide.md#62-新增本地工具) 与 [模块与本地工具系统设计](module-system.md)。

### 11.6 如何贡献代码

1. Fork 项目仓库
2. 创建分支：`git checkout -b feature/your-feature`
3. 编写代码，确保通过测试：`cd packages/backend && pytest`
4. 提交代码（遵循项目的 commit 规范）
5. 提交 Pull Request

代码规范：

- Python：`ruff check .` + `mypy src/`
- 前端：`pnpm lint` + `pnpm typecheck`

---

## 十二、开发工具推荐

### 12.1 IDE 推荐

- **Trae**（推荐）：字节出品的 AI IDE，基于 VS Code，内置 AI 助手，对中文友好
- **VS Code**：通用 IDE，插件生态丰富
- **PyCharm Professional**：Python 开发利器（付费）

### 12.2 必装插件

**VS Code / Trae 必装插件**：

| 插件                      | 说明                      |
| ------------------------- | ------------------------- |
| Python                    | Python 语言支持           |
| Pylance                   | Python 类型检查与智能提示 |
| Ruff                      | Python lint（项目使用）   |
| SQLAlchemy                | SQLAlchemy 语法支持       |
| PostgreSQL                | PG 查询工具               |
| Redis                     | Redis 客户端              |
| YAML                      | YAML 文件支持             |
| Tailwind CSS IntelliSense | 前端 Tailwind 提示        |
| React Developer Tools     | React 调试                |
| ESLint                    | 前端 lint                 |

### 12.3 调试技巧

**后端调试**：

- 在 VS Code 中创建 `launch.json`：

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "AI Town Backend",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["src.main:app", "--reload", "--port", "8000"],
      "cwd": "${workspaceFolder}/packages/backend",
      "justMyCode": false
    }
  ]
}
```

- 在代码中打 breakpoint，按 F5 启动调试

**前端调试**：

- React Developer Tools 浏览器扩展
- TanStack Router Devtools（开发模式自动启用）

**数据库调试**：

- 直接在 VS Code 的 PostgreSQL 插件中执行 SQL
- 查看角色状态：`SELECT * FROM characters JOIN character_states ON characters.id = character_states.character_id;`
- 查看记忆：`SELECT id, content, importance, timestamp FROM memory_episodes WHERE character_id = '...' ORDER BY timestamp DESC LIMIT 10;`

### 12.4 数据库管理工具

- **pgAdmin**（推荐）：PostgreSQL 官方管理工具，功能全面
- **DBeaver**（推荐）：跨平台数据库管理工具，支持多种数据库
- **TablePlus**：界面美观的数据库管理工具（付费）
- **VS Code PostgreSQL 插件**：轻量级，直接在 IDE 中查询

### 12.5 Redis 管理工具

- **Redis Insight**（推荐）：Redis 官方可视化工具，支持查看键值、执行命令
- **Another Redis Desktop Manager**：跨平台 Redis 桌面客户端
- **VS Code Redis 插件**：轻量级

### 12.6 API 调试工具

- **Swagger UI**（内置）：http://localhost:8000/docs
- **Postman**：功能全面的 API 调试工具
- **Insomnia**：开源的 API 调试工具
- **curl / Invoke-RestMethod**：命令行工具

---

## 结语

恭喜你读完了整份新手指南！

现在你已经掌握了：

- AI Town 是什么、能做什么
- 核心概念（World Tick、Character Tick、记忆、反思、规划、Action、主动分享、群聊智能回复）
- 如何在本地完整启动项目（后端 + 前端 + 数据库 + Redis）
- 如何接入 QQ 机器人
- 如何创建角色并与它交互
- 如何排查常见问题
- 如何进阶学习

**下一步建议**：

1. 先把项目跑起来，导入 4 个预置角色
2. 观察角色在小镇里的行为（看 `/world` 和 `/characters/:id/actions`）
3. 和角色聊几句天，感受它的"记忆"能力
4. 接入 QQ 机器人，体验"主动分享"
5. 阅读源码，深入理解某个你感兴趣的子系统

如果遇到问题，先看第十章 FAQ，再看 `docs/` 下的设计文档。

祝你玩得开心！🏠✨
