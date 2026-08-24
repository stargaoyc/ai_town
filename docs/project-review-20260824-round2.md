# AI Town 全面复审报告·第二轮（2026-08-24，HEAD `0da5e79`）

> **文档定位**：本报告是对[首轮全面检查报告](project-review-20260824.md)（基线 `dabecc9`）发布后整改批
> （`dabecc9..0da5e79`，12 commits、44 文件、+1725/−911）的**逐项复核**，并在此基础上对全部维度重新评分。
> 复审方法：全部结论基于当前工作区源码的直接阅读（非采信 CHANGELOG 宣称），关键路径附 file:line 证据；
> 质量门禁在本地实际执行（ruff / mypy strict / pytest / pnpm lint / typecheck / vitest）。
>
> **严重度定义**（沿用首评）：P0=核心功能静默失效/发布阻断；P1=功能性 bug/承诺违约/结构性缺陷；
> P2=纵深防御缺口/文档漂移/性能隐患；P3=瑕疵。

---

## 一、执行摘要

**一句话结论：整改批是真实的、高质量的——首轮两项 P0 与全部 P1 均已落地且有代码证据支撑，十大维度平均分从 6.9 升至 8.1；剩余问题从「结构性裂缝」降级为「测试债 + 少量宣称偏差 + 两处部署卫生遗留」，项目已具备长期运行的基本盘。**

本轮复核最重要的三个判断：

1. **认知闭环已经闭合**（首轮 P0-1）：反思、日记、Person Memory 三条产物现已真实注入决策与对话 Prompt，
   占位符、取数逻辑、截断保护逐行核实无误。「角色更聪明了」从宣传语变成了代码事实。
2. **记忆可达性修复数学上成立**（首轮 P0-2）：指数衰减公式 `(sim*0.6 + importance*0.05) × (0.25 + 0.75·e^(-days/30))`
   使 22 天前的记忆保留约 61% 得分、180 天仍保 25% 下限，永不为负——「三个月前的重要事件不可达」问题消除。
3. **新的最大短板是测试债**：认知回流、计划落库、热度衰减、retention 等本次新增的核心链路**没有任何单元测试**
   （codegraph 覆盖分析证实 zero covering tests）；叠加集成测试探针缺陷（§三 N1），本地 `pytest` 出现 34 个
   error 而非预期 skip——「改认知链路靠人肉回归」成为当前最现实的回归风险。

### 维度评分对照

| 维度 | 首轮 | 二轮 | 变化主因 |
|---|:---:|:---:|---|
| 项目定位与演化 | 9 | **9** | 定位未变，无需重评；调度架构持续支撑「世界驱动陪伴」 |
| 分层架构与模块边界 | 7 | **8.5** | main.py 瘦身、循环下沉 scheduler、core→messaging 回调解耦全部落地 |
| 多智能体交互与世界模拟 | 6 | **7** | N+1 修复、场景描述注入、对话 4 句承接、事件去重基线持久化；群体动力学仍缺 |
| 认知机制完备性 | 4.5 | **8** | 三断流接通 + 指数衰减 + planChanges 落库 + 动态检索 + PM ORM 化；扣测试缺位与合并语义 |
| ReAct 工具调用 | 8 | **8.5** | 工具开关 5s TTL 缓存消除热路径 Redis 往返；死代码清理 |
| 数据持久化设计 | 8 | **8.5** | retention 分级清理落地、version 自增；乐观锁仍未贯通 CAS |
| 全链路可观测性 | 9 | **9.5** | Langfuse↔OTel trace 关联、DB_QUERY_DURATION 真实埋点——近乎无可挑剔 |
| 部署与工程化 | 7.5 | **8** | 镜像 tag 三方统一、启动自动迁移、PyJWT 替换、CI 接入前端测试；凭据硬编码残留 |
| 前端工程化与 UX | 6.5 | **7.5** | vitest 冒烟测试 + CI 接入、类型边界标注；仍无组件级/E2E 测试 |
| 长期运行风险治理 | 3.5 | **7.5** | 记忆膨胀治理（retention+热度衰减+写入去重）从零到一；IT 探针缺陷与新链路测试缺位是新账单 |
| **均值** | **6.9** | **8.1** | |

---

## 二、整改逐项复核（24 项）

判定口径：✅=代码证实与宣称一致；⚠️=已修但有保留意见/宣称偏差；⏸=声明暂缓（评估其合理性）。

### 架构域（首评 §三）

| # | 整改项 | 判定 | 证据与备注 |
|---|---|:---:|---|
| 1 | 删除 `_cycle_probe.py` | ✅ | 文件已不存在 |
| 2 | core→messaging 反向依赖解耦 | ✅ | 回调注册 `main.py:225`（`set_proactive_share_handler(run_tick_proactive_share)`）、消费 `tick.py:1336`（`get_proactive_share_handler`）、定义 `runtime.py:57,138,226`；None 有 debug 日志守卫。Core 层不再 import messaging |
| 3 | main.py 瘦身 | ✅ | 约 500 行，lifespan 组装为主；三个后台循环下沉 `scheduler/loops.py`；AuthMiddleware 迁 `auth/middleware.py:118` |
| 4 | API 层直连 repository ×5 | ⏸ | `admin/characters/memory/messages/world.py` 仍在直连。暂缓理由（FastAPI 常规模式、重构收益低）成立，维持暂缓判断 |

### 认知域（首评 §五，原 P0×2 + P1×4）

| # | 整改项 | 判定 | 证据与备注 |
|---|---|:---:|---|
| 5 | **P0-1a 反思注入决策 Prompt** | ✅ | `tick.py:322-335` 取最近 5 条 → render 参数（`tick.py:523`）；`decision.yaml` 含 `{reflections}` |
| 6 | **P0-1b 日记注入决策 Prompt** | ✅ | `tick.py:337-350`（300 字符截断）；`{diary}` 占位符确认 |
| 7 | **P0-1c Person Memory 注入对话** | ✅ | `messaging/service.py:519` 调用 `get_relevant_context()`（首轮 grep 证实全库零调用，现已有消费方）；`chat.yaml` 含 `{person_memory}` |
| 8 | **P0-2 时间衰减改指数公式** | ✅ | `memory_repo.py:324-327`：`final_score=(sim*0.6+importance*0.05)*(0.25+0.75*exp(-days/30))`。心算验证：22 天保留 61%，90 天 28.7%，180 天 25.2%，下限 25% 永不为负。数学正确 |
| 9 | planChanges 死功能落库 | ✅ | `tick.py:980-983` → `_apply_plan_changes`（`tick.py:1217-1249`）；`update_plan_scoped(plan_id, character_id)` 以 character_id 约束防跨角色篡改；progress 钳制 0-100；单条失败仅告警不中断 |
| 10 | 记忆写入去重 | ⚠️ | `episode_service.py:144-151` + `exists_recent_duplicate`：24h 窗口内归一化空白后**精确匹配**去重。改写式重复（同义不同词）不拦截——比「无去重」进步明显，但距「相似度去重」仍有距离（见 §三 N7） |
| 11 | 检索 query 动态化 | ✅ | `tick.py:301-308`：拼入时段/情绪/计划标题语义信号 |
| 12 | PersonMemory ORM 化 + preferences + 热度衰减 | ⚠️ | ORM 模型端到端 ✅；preferences JSONB 真实写入 ✅；热度衰减循环每 6h 将 >14 天未交互者减半（`loops.py:280-295`）✅；回复后异步更新经 `spawn_background`（`service.py:424-449`）✅。**但**「增量合并」宣称与实现有差：更新仍是 LLM 基于 existing_content+本轮对话**全文重写**单槽内容，telephone game 仅靠 prompt 措辞缓解，结构上未消除（见 §三 N5） |

### 世界引擎与并发域（首评 §四/§七）

| # | 整改项 | 判定 | 证据与备注 |
|---|---|:---:|---|
| 13 | nearby_characters N+1 修复 | ✅ | 单次关系批量查询复用（`tick.py` perceive 路径），与整改说明一致 |
| 14 | decision prompt 注入真实场景描述 | ✅ | 场景描述来自 scenes 配置注入，`scenes=""` 占位消除 |
| 15 | chat_with 对话升级 | ⚠️ | `configs/prompts/chat_with.yaml`：要求一次生成 4 句（发起方/对方×2），并明确「第二轮应承接第一轮话题自然深入」。**实现是单次 LLM 调用生成整段 4 句**（800 字符截断），并非两次独立往返。工程上合理（省 token、保证连贯性），但 CHANGELOG「两轮往返对话」措辞夸大了机制变化（见 §三 N6） |
| 16 | world_events 去重基线持久化 | ✅ | `engine.py:57-58`（`BASELINE_KEY="world:events:baseline"`）、`:97-109` 启动恢复、`:472-490` 写后回写 Redis——重启后不再重复写入未变化事件 |
| 17 | update_state 自增 version | ⚠️ | `character_repo.update_state`：SQL 端 `version=version+1` 且 `fields.pop("version")` 防客户端伪造——自增属实。**但**这只是审计版本号，并未做「读取-比对-条件更新」的乐观锁 CAS：Tick 与 API 并发写同一角色仍是 last-write-wins（首评 §七 P1 的并发本质未变，宣称也确实只说「自增」，见 §三 N4） |
| 18 | 对话即时写入对方上下文 | ⏸ | 暂缓理由（记忆检索已是回流路径，避免双写不一致）技术上成立 |

### 数据与部署域（首评 §六-§十）

| # | 整改项 | 判定 | 证据与备注 |
|---|---|:---:|---|
| 19 | 工具启用状态 TTL 缓存 | ✅ | `registry.py:183-225`：5s monotonic TTL 缓存，热路径 Redis 往返消除；无主动失效函数，切换工具最长 5s 生效延迟（可接受，§三 N8） |
| 20 | memory_retention_loop 分级清理 | ✅ | `loops.py:304-364`：importance≤3 超 90 天删、4-6 超 180 天删、≥7 永久保留；`MEMORY_RETENTION_ENABLED` 开关；注释明确解释 HASH 分区无法 drop 只能应用层删除（符合「注释解释为什么」规范） |
| 21 | 容器启动自动迁移 | ✅ | `Dockerfile:57` CMD 内 `alembic upgrade head && uvicorn...`；注释已提示多副本需改独立 Job |
| 22 | Langfuse 附 OTel trace id / DB 埋点 | ✅ | `langfuse_tracing.py:31-41` 读当前 span trace_id、`:74-76,:113-115` 注入 metadata（Langfuse↔Jaeger 可互查）；`DB_QUERY_DURATION.observe` 落在 `db/session.py:44` 真实查询路径 |
| 23 | PyJWT 替换 / Redis 版本统一 / tag 固定 | ✅ | `pyproject.toml`：`pyjwt>=2.8`，python-jose/passlib 已移除；compose 全部镜像固定版本（pgvector:pg18、redis:8-alpine、prometheus v2.53.0、loki 3.0.0、jaeger 1.62.0、alloy v1.0.0、grafana 12.0.0）且 README/CI/compose 三方一致 |
| 24 | vitest 冒烟 + 类型边界标注 | ✅ | `queries.test.ts`（queryKeys 契约测试，防 invalidateQueries 静默失效——测试意图注释清晰）、`auth.test.ts`（登录成功/失败/登出/localStorage 持久化 4 用例，fetch mock）；CI frontend job 已接入 `pnpm test -- --run` |

### 质量门禁实测（本机，2026-08-24）

| 命令 | 结果 |
|---|---|
| `pnpm run lint` / `typecheck` | ✅ / exit=0 |
| `pnpm test`（vitest） | ✅ 6 passed（2 文件） |
| `uv run ruff check src/ tests/` + `format --check` | ✅ 全过 |
| `uv run mypy src/ tests/`（strict） | ✅ 163 文件零错误 |
| `uv run pytest` | ⚠️ **321 passed + 34 errors**（137s）——errors 全部为集成测试因本机 PG 握手失败抛 ConnectionError，**而非预期的自动 skip**，根因见 §三 N1 |

> 说明：整改批宣称「pytest 355 passed」应为在有 PG/Redis 的环境下运行（321+34=355 收集数吻合）。
> 本机 34 个 error 不是回归，而是探针缺陷让「环境缺失」伪装成「测试失败」。

---

## 三、本轮新发现问题

### P2（纵深防御缺口 / 测试债）

**N1｜集成测试可达性探针只做 TCP 检查**（`tests/integration/conftest.py:46-53`）
探针用 TCP 连接成功即认定 PG 可用。本机实测：TCP 通但 PostgreSQL 握手失败 → 34 个 IT 抛 ConnectionError
计为 **error** 而非 skip。后果：①「本地一键测试」承诺失效；②真实测试失败与环境问题的信号被混在一起，
违背该项目一贯的 fail-fast 品味。建议改为执行 `SELECT 1` 的真实握手探测。

**N2｜认知新链路零测试覆盖**
本次整改新增的核心行为均无单元测试（codegraph 覆盖分析逐一证实）：`_apply_plan_changes`（含跨角色防篡改、
progress 钳制两个安全语义）、`get_relevant_context` 注入路径、heat_decay 循环、retention 分级规则、
`_save_world_events` 去重基线、指数衰减公式的边界（days=0/负数/超老）。`test_memory_repo_it.py` 仅覆盖
search_hybrid/final_score/exists_recent_duplicate。**整改批最大的质量风险不是写错，而是无人能安全地再改动这些链路。**

**N3｜compose 凭据硬编码残留**（首轮 §九 P1 未修完的部分）
`docker-compose.yml:27` `POSTGRES_PASSWORD: password`、`:181` Grafana `admin/admin123` 仍未参数化
（首评 P0 冲刺清单第 3 条明确列了 `${POSTGRES_PASSWORD:?}` 参数化，整改批只完成了版本统一一半）。
另 `backend.environment.DATABASE_URL` 硬编码凭据优先级高于 env_file，`.env` 在 compose 下对该项失效
（双真相源）。镜像 tag 问题已修，凭据问题原样保留。

**N4｜乐观锁只走了一半**
version 自增（#17）解决了「变更可追溯」，但 Tick 与 API 并发写同一角色的 last-write-wins 窗口仍在：
`update_state` 无 `WHERE version=:expected` 条件更新，10 分钟对账以 Redis 为准仲裁仍可能回滚 API 刚写入的合法变更。
QQ 高频群聊场景下该窗口被放大（首评 R2 风险原文有效）。

### P3（瑕疵 / 文档措辞）

**N5｜PersonMemory「增量合并」实为 LLM 全文重写**：单槽 unique 行每次交互整体重写，早期细节稀释的结构性风险
仍在，靠 merge prompt 措辞缓解。建议后续引入 append-only 事实条目 + 定期压缩的两层结构。

**N6｜chat_with「两轮往返」措辞 vs 单次生成 4 句**：机制上是一次调用产出承接式 4 句。建议 CHANGELOG/README
改为「四句承接式对话（单次生成）」以免误导后续维护者对 token 成本的预期。

**N7｜写入去重为归一化精确匹配**：改写式重复不拦截。低成本改进：对归一化文本再做一次 pgvector 相似度
（阈值 ~0.95）或 trigram 相似度判断。

**N8｜工具开关缓存无主动失效**：管理端切换工具后最长 5s 生效。可接受；若要即时生效，在 toggle API 里置空
模块级 `_enabled_cache` 即可（一行改动）。

**N9｜杂项**：`pyproject.toml` 中 `[dependency-groups] dev=["ruff"]` 与 `[optional-dependencies] dev` 重复定义；
CI `pnpm/action-setup@v4 version: 9` 与 README「pnpm 11+」漂移；`docker-compose.yml` backend 的 DATABASE_URL
与 `.env` 构成双真相源（并入 N3 一并处理）。

---

## 四、维度重评详注（仅展开变化 ≥1 分的维度）

### 认知机制完备性 4.5 → 8

+3.5 的构成：三条断流接通（+1.5，产品承诺兑现）、指数衰减（+0.5，老年记忆可达）、planChanges 落库带归属
校验（+0.5，死功能复活且防了越权）、检索 query 动态化 + 写入去重（+0.5）、PersonMemory 从裸 SQL 升级 ORM +
preferences 落库 + 热度衰减（+0.5）。未给满的原因：新链路零测试（N2）、合并语义保留全文重写（N5）、
reflection 仍是「最近 20 条」批次归纳无法跨期主题化（首评 §5.3 未动，属下一阶段）。

### 长期运行风险治理 3.5 → 7.5

+4 的构成：retention 分级清理（HASH 分区膨胀有了应用层出口）、热度衰减、写入去重、world_events 基线持久化、
reconcile 双向对账闭环核实（`core/reconcile.py`：missing_key→PG 回灌 Redis、value_drift→Redis 修 PG，
指标真实递增，由 loops 调度 + main lifespan 管理）。未满分原因：N1 探针缺陷、N4 并发窗口、以及首评 R1 量化
（5 角色 ≈530 万行/年）中 retention 只解决「删除」未解决「低价值压缩归档」——90 天内的膨胀速率不变。

### 分层架构 7 → 8.5

main.py 从 844 行臃肿入口变为纯组装层；后台循环、认证中间件各归其位；core→messaging 依赖方向修正为运行时
回调注入。扣分点仅剩 API 层直连 repository（暂缓合理）与 scheduler/loops.py 内部的延迟 import（循环依赖
规避手段，可接受但值得留注释）。

### 可观测性 9 → 9.5

两套 trace 体系打通（Langfuse metadata 携带 OTel trace_id，可从 Jaeger 跳 Langfuse 反查）；DB_QUERY_DURATION
从「定义未埋点」变为 session 层真实观测。剩余 0.5 分缺口：`trace_character_tick` 仍未串联同 Tick 内多次
LLM 调用的父子关系（首评 §八 P2，未在本批范围）。

---

## 五、残余风险与下一步建议（优先级排序）

### 立即（合计 ≈1 人日）

1. **修 N1 探针**：TCP 探测改为 `SELECT 1` 真实握手（conftest.py 一处改动），恢复「本地无 DB → 自动 skip」语义；
2. **修 N3 凭据参数化**：`${POSTGRES_PASSWORD:?err}` / Grafana 密码走 env，backend DATABASE_URL 移出
   environment 改由 .env 单一供给；
3. **N8 一行失效**：toggle API 内 `_enabled_cache = None`。

### 两周内（测试还债，配合 N2）

4. 为 `_apply_plan_changes`（重点：跨角色篡改拒绝、progress 边界）、retention 分级边界、heat_decay、
   指数衰减公式边界值补单元测试——这些是纯函数/易构造场景，性价比极高；
5. `update_state` 贯通 CAS：`WHERE character_id=:id AND version=:expected`，冲突时重读重试一次（N4）；
6. 文档措辞校准：chat_with「四句承接式（单次生成）」（N6）、pnpm 版本对齐（N9）。

### 战略级（沿袭首评 §十二，状态更新）

7. 群体动力学实验（`related_characters` 字段仍是预留状态，传闻传播是提升「小镇感」性价比最高的一步）；
8. reflection 跨期主题归纳（当前「最近 20 条」批次限制了高层认知的质量上限）；
9. 低价值记忆压缩归档（retention 只删不压，90 天窗口内的膨胀速率未变）；
10. LangChain 依赖去留评估（首评 §十一判断仍有效：实际只用 ChatOpenAI 包装，抽象收益低）；
11. Redis 清空冷启动恢复演练（rehydration 闭环验证仍未做过）。

---

## 六、总评

这一轮整改的成色可以用一个细节概括：`update_state` 的 docstring 里写着「version 自增作为手动版本号递增，
当前并发写下方无法感知状态变化度（审查-P1 备注）」——**修复者在承认修复不完整的同时留下了为什么**。
这种诚实贯穿整个整改批：暂缓项给了理由，部分修复（去重、合并）没有伪装成彻底修复。

对照首评的两个系统性诊断：**「认知闭环只有前半环」已经修复**——反思/日记/Person Memory 的生产端与消费端
首次形成完整回路，README 的承诺与代码事实对齐；**「为当下正确、为未来裸奔」大幅改善**——记忆生命周期治理
从零到一（分级清理+热度衰减+写入去重），六个月后必然爆发的账单大部分已预付。

剩余的问题性质也变了：不再是架构裂缝，而是**测试债**（新认知链路零覆盖 + IT 探针缺陷）和**收尾卫生**
（凭据参数化、CAS 贯通、文档措辞）。按 §五 清单执行约一周，本项目即可从「架构示范品 + 可长期运行的陪伴
产品雏形」进入「可放心持续演进」的状态。

**二轮总评：8.1 / 10。整改真实、克制、可追溯；下一个杠杆点是测试，不是功能。**

---

## 附记：本报告问题清单的修复状态（2026-08-24 当日第二批）

| # | 问题 | 处置 | 说明 |
|---|---|---|---|
| N1 | IT 探针仅 TCP 检查 | ✅ 已修 | `conftest.py` 改 asyncpg `SELECT 1` + Redis `PING` 真实握手；本地实测从 34 error 变为 11 skip |
| N2 | 认知新链路零测试 | ✅ 部分还债 | 新增 `_apply_plan_changes` 安全语义单测 ×5（并借此发现隐式 update 兜底缺陷，已修）；CAS 与去重语义补 IT ×7。retention/heat 循环测试需先做可测性抽取，列入后续 |
| N3 | compose 凭据硬编码 | ✅ 已修 | 三处参数化（PG 密码/Grafana 密码/backend URL 共用变量），`.env.example` 补文档，顺带清除残留的 PgBouncer 失真注释 |
| N4 | 乐观锁未贯通 CAS | ✅ 已修 | `update_state(expected_version=...)` 条件更新 + `update_state_cas` 冲突重读重试一次；API 移动端点接入；Tick 主链路维持无条件写（Redis 为真相源） |
| N5 | PM 单槽全文重写 | ⏸ 维持现状 | 复核发现 merge Prompt 已含保留规则（person_memory.yaml），Prompt 级缓解本就到位；结构性两层方案仍列战略项 |
| N6 | 「两轮往返」措辞失真 | ✅ 已修 | CHANGELOG 改「四句承接式对话（单次生成）」 |
| N7 | 去重拦不住改写式重复 | ⚠️ 试验后回退 | pg_trgm 对中文实测无效（真实改写对相似度仅 0.31–0.40，无关对 0.00），阈值不可调平；正确方案是 embedding worker 落向量后余弦比对，列入待办而非硬塞。现状以钉住语义的测试固化 |
| N8 | 工具开关缓存无主动失效 | ✅ 已修 | `invalidate_enabled_cache()` 接入 toggle API |
| N9 | 工程杂项 | ✅ 已修 | dev 依赖收敛 `[dependency-groups]`（CI 同步去 `--extra dev`）；pnpm 三方对齐 11（packageManager/CI/Dockerfile） |

修复批验证基线：ruff check/format 全过、mypy strict **164 文件零错误**、pytest **357 passed + 11 skipped**（此前 321 passed + 34 errors）、前端 lint/typecheck/vitest 全过、`docker compose config` 校验通过。
