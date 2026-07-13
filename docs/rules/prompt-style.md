# Prompt 规范

> 本文档定义 aitown 项目所有 LLM Prompt 的编写、维护与注入规范。
>
> Prompt 是业务逻辑的一部分，不是"随便写的文本"。Prompt 变更需经过与代码变更同等的 review 流程。
>
> 配套文档：[implementation-style.md](implementation-style.md) · [domain-design-style.md](domain-design-style.md) · [refactor-style.md](refactor-style.md)

---

## 一、Prompt 维护位置

### 1.1 单一真相源

**所有 Prompt 模板必须外置到 `configs/prompts/*.yaml`。** 禁止在 Python 代码里内嵌 Prompt 字符串。

| 规则 | 说明 |
|------|------|
| 模板文件位置 | `configs/prompts/{name}.yaml` |
| 加载入口 | `src/llm/prompts.py` 的 `PromptTemplates` |
| 渲染方式 | `prompts.render("decision", name=..., personality=...)` |
| 热更新 | `prompts.reload()` 重新加载 YAML |
| 兜底默认 | `PromptTemplates` 内置 `DEFAULT_*_PROMPT`，仅在 YAML 缺失时使用 |

### 1.2 YAML 文件格式

每个 Prompt 对应一个 YAML 文件，格式固定：

```yaml
name: <模板名>          # 必须与文件名一致，用于 render() 调用
template: |
  [角色档案]
  姓名: {name}
  性格: {personality}

  [输出格式]
  请输出 JSON:
  {{ "action": "<action_id>" }}
```

| 规则 | 说明 |
|------|------|
| `name` 字段 | 必须与文件名（去 `.yaml`）一致 |
| `template` 字段 | 使用 Python `str.format()` 占位符 `{key}` |
| 转义 JSON 大括号 | 用 `{{` 和 `}}` 表示字面量 `{` 和 `}` |
| 占位符命名 | 用 `snake_case`，与代码变量名一致 |
| 文件编码 | UTF-8 |

### 1.3 现有 Prompt 清单

| 文件 | name | 用途 | 调用方 |
|------|------|------|--------|
| `configs/prompts/chat.yaml` | `chat` | 角色回复用户消息 | `MessageService` |
| `configs/prompts/decision.yaml` | `decision` | 角色 Action 决策 | `CharacterTickEngine` |
| `configs/prompts/reflection.yaml` | `reflection` | 角色反思生成 | `ReflectionService` |

> 新增 Prompt 必须在以上清单中登记，并在 `PromptTemplates` 中添加默认兜底。

### 1.4 禁止事项

| 禁止 | 原因 |
|------|------|
| 在 `.py` 文件里写 Prompt 字符串 | 无法热更新，无法集中 review |
| 在 Prompt 中硬编码角色信息 | 角色信息必须通过 `{name}`/`{personality}` 占位符注入 |
| 多个 Prompt 共用一个 YAML 文件 | 一文件一 Prompt，便于 diff 与回滚 |
| 在 Prompt 中写 Python 逻辑 | Prompt 是文本，不是代码 |

---

## 二、视角规范

### 2.1 统一第二人称

**任务指令段使用第二人称「你」指代角色。** 让 LLM 以角色身份行事，而非以"助手"身份描述角色。

| 场景 | 视角 | 示例 |
|------|------|------|
| 任务指令 | 第二人称「你」 | `你在为自己的生活做决策，需要选择一个 Action` |
| 输出格式说明 | 第二人称「你」 | `请输出 JSON，包含你选择的 action` |
| 角色档案 | 第三人称陈述 | `姓名: {name}` / `性格: {personality}` |
| 世界状态 | 第三人称陈述 | `时间: {world_time}` / `天气: {weather}` |
| 候选项描述 | 第三人称陈述 | `可选 Action: {candidates}` |

### 2.2 视角一致性

**同一 Prompt 内不得混用视角。** 特别是不要在「你」和「角色」之间切换。

```yaml
# ✅ 统一第二人称
template: |
  你正在为自己的生活做决策。
  当前你的状态：精力 {energy}/100，情绪 {mood}。
  请从候选 Action 中选择一个最合适的。

# ❌ 视角混用
template: |
  角色正在做决策。你需要从候选 Action 中选择一个。
  她的当前状态：精力 {energy}/100。
```

### 2.3 禁止"助手"口吻

**不得出现"作为 AI 助手"、"你是一个 AI"等表述。** 角色不是助手，是有自己生活的人。

| 禁止 | 替代 |
|------|------|
| `你是一个 AI 助手，请帮助用户...` | `你是 {name}，一个 {occupation}...` |
| `作为 AI，你应该...` | 直接描述角色应有的行为 |
| `请根据用户的需求...` | `你在为自己的生活做决策` |

---

## 三、人设与任务分离

### 3.1 分离原则

**角色人设（稳定身份）与任务指令（动态行为）必须分离。** 人设跨场景复用，任务指令按场景变化。

| 层 | 内容 | 稳定性 | 复用范围 |
|----|------|--------|----------|
| 人设层 | 姓名/性格/背景/说话风格 | 极稳定（角色卡级别） | 聊天 + 决策 + 反思 |
| 任务层 | 当前要做什么决策/生成什么输出 | 按场景变化 | 单场景 |
| 状态层 | 当前时间/天气/位置/精力 | 每 Tick 变化 | 单次调用 |

### 3.2 分离实现

当前 aitown 的 Prompt 将三层混在一个 `template` 里。建议逐步拆分：

```yaml
# configs/prompts/decision.yaml
name: decision
template: |
  # === 人设层 ===
  [角色档案]
  姓名: {name}
  性格: {personality}
  背景: {backstory}

  # === 任务层 ===
  [任务]
  你正在为自己的生活做决策，需要从候选 Action 中选择一个最合适的。
  行为必须符合[世界状态]中的时间和天气。
  行为必须符合角色人设和当前状态（精力低时倾向休息，饥饿时倾向就餐）。

  # === 状态层 ===
  [当前状态]
  位置: {location}
  精力: {energy}/100
  情绪: {mood}

  [世界状态]
  时间: {world_time}
  天气: {weather}

  [候选 Action]
  {candidates}

  # === 输出层 ===
  [输出格式]
  请输出 JSON:
  {{ "action": "<action_id>", "reason": "<理由>" }}
```

### 3.3 聊天 vs 决策的人设差异

**聊天场景与决策场景对角色的"调用方式"不同。** 不要用同一份人设描述同时服务两个场景。

| 场景 | 需要的人设信息 | 不需要的人设信息 |
|------|----------------|------------------|
| 聊天 | 说话风格、情绪表达、关系理解 | 决策偏好、取舍逻辑 |
| 决策 | 生活节奏、取舍偏好、状态优先级 | 说话温度、颜文字规则 |
| 反思 | 性格基调、情绪倾向 | 决策细节、说话风格 |

> 参考 yuiju 的做法：`characterPersonalityPrompt`（聊天人格）与 `characterDecisionPrompt`（决策版人设）分离。详见 [yuiju-comparison.md](../yuiju-comparison.md)。

---

## 四、工程概念不外泄

### 4.1 禁止外泄的工程概念

**面向用户的回复（聊天/主动分享）不得暴露任何工程概念。** 决策 Prompt 内部可使用工程概念，但不得出现在角色对外输出中。

| 禁止外泄的概念 | 替代表述 |
|----------------|----------|
| `action_id` / `Action` | 自然描述行为（"去咖啡店"而非 `move_home_to_cafe`） |
| `schema` / `params` | 不提及 |
| `field` / `field_name` | 不提及 |
| `tick` / `tick_id` | 不提及 |
| `Redis` / `PG` / `pgvector` | 不提及 |
| `LLM` / `model` / `token` | 不提及 |
| `prompt` / `template` | 不提及 |
| `executor` / `precondition` | 不提及 |
| `MemoryEpisode` / `Reflection` | 自然说"回忆""想起""反思" |
| `conversation_id` / `session_id` | 不提及 |
| `trace_id` / `span_id` | 不提及 |

### 4.2 决策 Prompt 的特殊许可

**决策 Prompt（不直接面向用户）可使用 `action_id` 作为候选项标识**，因为 LLM 需要在结构化输出中返回 `action` 字段。但：

| 允许 | 禁止 |
|------|------|
| `[候选 Action]` 列表中用 `action_id: 描述` | 在 `reason` 字段中用 `action_id` |
| 输出 JSON 中 `"action": "<action_id>"` | 在 `reason` 中说"我选择了 action_id=xxx" |

```json
// ✅ reason 用自然语言
{
  "action": "move_home_to_cafe",
  "reason": "下午想去咖啡店喝杯咖啡放松一下"
}

// ❌ reason 用工程概念
{
  "action": "move_home_to_cafe",
  "reason": "执行 move_home_to_cafe，参数 from=home to=cafe"
}
```

### 4.3 聊天 Prompt 的严格约束

**聊天回复必须像真人发的消息，不能暴露任何"被程序处理"的痕迹。**

| 禁止 | 示例 |
|------|------|
| 提及 Action 系统 | "我刚执行了吃饭 Action" |
| 提及状态数值 | "我的体力现在是 80/100" |
| 提及记忆检索 | "让我检索一下记忆" |
| 提及 LLM 调用 | "让我用 LLM 生成回复" |
| 暴露 JSON 结构 | "我的回复是 {response: ...}" |
| 用括号描写动作 | "（微笑）你好" / "（走向咖啡店）" |

---

## 五、世界状态注入规范

### 5.1 注入内容

**世界状态必须作为「事实」注入 Prompt，不得让 LLM 自行编造。** 以下信息必须从真相源注入：

| 信息 | 真相源 | 注入位置 |
|------|--------|----------|
| 虚拟时间 | Redis `world:state.time` | `[世界状态]` 段 |
| 天气 | Redis `world:state.weather` | `[世界状态]` 段 |
| 当前场景 | Redis `char:{id}:state.location` | `[当前状态]` 段 |
| 角色精力 | Redis `char:{id}:state.stamina` | `[当前状态]` 段 |
| 角色饱腹 | Redis `char:{id}:state.satiety` | `[当前状态]` 段 |
| 角色情绪 | Redis `char:{id}:state.mood` | `[当前状态]` 段 |
| 候选 Action | `ActionRegistry.get_candidates()` | `[候选 Action]` 段 |
| 相关记忆 | `RetrievalService.retrieve()` | `[相关记忆]` 段 |
| 当前计划 | `PlanRepository.list_active()` | `[当前计划]` 段 |

### 5.2 冲突优先级

**当 Prompt 中的世界状态与对话历史/用户描述冲突时，以世界状态为准。** 必须在 Prompt 中显式声明这一优先级。

```yaml
# 必须在 Prompt 中包含类似约束
[严格约束]
- 必须严格按照[世界状态]中的虚拟时间和天气进行回复，不得自行编造日期/时间/天气/季节等内容
- 严格以世界状态中的事实为准，过往对话中的信息可能有误，两者冲突时以世界状态为准
- 回复中涉及的时间/日期/天气等信息，必须与[世界状态]完全一致
```

### 5.3 注入格式

**世界状态注入必须用结构化文本块，不用 JSON。** 结构化文本块对 LLM 更友好，且节省 Token。

```yaml
# ✅ 结构化文本块
[世界状态]
时间: 2026-07-13 14:30 周日
天气: 晴
场景: 咖啡店(cafe)
开放: 是 | 拥挤度: 23/100 | 在场: 2人

# ❌ JSON 注入（Token 多，LLM 解析慢）
[世界状态]
{"time": "2026-07-13 14:30", "weather": "sunny", "scene": "cafe", "open": true, "crowdedness": 23}
```

### 5.4 候选 Action 注入格式

**候选 Action 必须用编号列表，每项含 `action_id` + 自然语言描述 + 资源影响。**

```yaml
[候选 Action]
1. sleep: 睡觉。恢复 40 体力，消耗 10 饱腹度。[耗时 480 分钟]
2. eat_at_home: 在家吃饭。恢复 25 饱腹度，花费 20 金币。[耗时 30 分钟]
3. move_home_to_school: 从家前往学校。[体力-2][耗时 5 分钟]
```

| 规则 | 说明 |
|------|------|
| 编号从 1 开始 | 便于 LLM 引用 |
| `action_id` 在前 | 结构化输出时返回 `action_id` |
| 自然语言描述在中 | LLM 理解行为含义 |
| 资源影响在后 | 用 `[体力-2]`/`[耗时 5 分钟]` 简洁标注 |
| 不展示 precondition | precondition 由代码过滤，不进 Prompt |

### 5.5 记忆注入格式

**记忆注入必须标注来源与时间，避免 LLM 混淆事实与对话。**

```yaml
[相关记忆]
- [2026-07-12 15:00] 在咖啡店遇到了小春，她请我喝了一杯拿铁。
- [2026-07-11 10:00] 在图书馆借了一本轻小说，还没看完。
- [2026-07-10 18:00] 和小春约好周末一起去海岸散步。
```

| 规则 | 说明 |
|------|------|
| 每条记忆独立一行 | 便于 LLM 逐条理解 |
| 标注时间 | 帮助 LLM 区分新旧记忆 |
| 用自然语言描述 | 不暴露 MemoryEpisode 等工程概念 |
| 不注入原始 JSON | Token 浪费且 LLM 解析慢 |

---

## 六、输出格式规范

### 6.1 结构化输出

**所有决策类 Prompt 必须要求 LLM 输出 JSON。** 聊天类 Prompt 也应输出结构化 JSON 以便下游处理。

| Prompt | 输出格式 | 必填字段 |
|--------|----------|----------|
| `chat` | JSON | `response`（回复内容） |
| `decision` | JSON | `action`、`reason` |
| `reflection` | JSON | `reflection`、`insights` |

### 6.2 JSON 输出约束

```yaml
[输出格式]
请输出 JSON:
{{ "action": "<action_id>", "reason": "<理由>", "params": {{...}}, "duration": <分钟> }}
```

| 规则 | 说明 |
|------|------|
| 用 `{{` `}}` 转义大括号 | YAML + Python `str.format()` 要求 |
| 字段名用 `snake_case` | 与 Python 代码一致 |
| 必填字段显式列出 | 让 LLM 知道必须返回哪些字段 |
| 可选字段标注 | `<可选动作>` 等 |
| 不要求 LLM 输出注释 | JSON 不支持注释，LLM 可能乱加 |

### 6.3 聊天回复的额外字段

聊天 Prompt 除 `response` 外，可要求 LLM 输出元信息：

```yaml
请输出 JSON:
{{ "response": "<回复内容>", "emotion": "<情绪>", "action": "<可选动作>" }}
```

| 字段 | 用途 | 是否必填 |
|------|------|----------|
| `response` | 回复正文 | 必填 |
| `emotion` | 当前情绪（用于更新状态） | 可选 |
| `action` | 角色在回复时做的动作（如"微笑"） | 可选 |

> `emotion` 和 `action` 不得出现在回复正文中，仅供状态更新使用。

---

## 七、Prompt 变更流程

### 7.1 变更步骤

| 步骤 | 说明 |
|------|------|
| 1. 修改 YAML | 直接编辑 `configs/prompts/*.yaml` |
| 2. 本地测试 | 用 `prompts.render()` 验证渲染结果 |
| 3. LLM 验证 | 用真实 LLM 调用验证输出质量 |
| 4. Review | Prompt 变更需经过 review，与代码变更同等流程 |
| 5. 热更新 | 生产环境可通过 `prompts.reload()` 热更新 |

### 7.2 变更检查清单

- [ ] 占位符是否与代码中 `render()` 传入的参数一致？
- [ ] 转义的 `{{` `}}` 是否正确？
- [ ] 是否有工程概念外泄到面向用户的 Prompt？
- [ ] 世界状态注入是否完整（时间/天气/场景/状态）？
- [ ] 冲突优先级约束是否保留？
- [ ] 输出格式是否与 `DecisionResult`/`ActionResult` 模型一致？
- [ ] Token 长度是否在合理范围（< 2000 Token）？

---

## 八、常见反模式

### 8.1 Prompt 膨胀

**症状**：Prompt 越来越长，塞入过多约束/示例/历史。

**危害**：Token 成本上升，LLM 注意力分散，关键约束被忽略。

**修复**：拆分为 system prompt（稳定约束）+ user prompt（动态状态）。稳定约束走 Prompt 缓存。

### 8.2 约束矛盾

**症状**：不同段落的约束互相矛盾（如"要简洁"又"要详细解释"）。

**危害**：LLM 行为不稳定，难以预测。

**修复**：统一约束段，删除矛盾项。

### 8.3 示例污染

**症状**：在 Prompt 中给 LLM 看示例，但示例风格被 LLM 机械模仿。

**危害**：输出千篇一律，失去自然感。

**修复**：少用示例，多用约束描述。必须用示例时，提供多种风格。

### 8.4 状态滞后

**症状**：Prompt 中的世界状态与实际状态不一致（如时间已推进但 Prompt 还用旧时间）。

**危害**：角色行为与世界脱节。

**修复**：严格在 LLM 调用前从 Redis 读取最新状态注入。

---

## 相关文档

| 主题 | 文档 |
|------|------|
| 代码风格规范 | [implementation-style.md](implementation-style.md) |
| 领域设计规范 | [domain-design-style.md](domain-design-style.md) |
| 重构规则 | [refactor-style.md](refactor-style.md) |
| yuiju 对比（Prompt 分层参考） | [../yuiju-comparison.md](../yuiju-comparison.md) |
| 角色设计 | [../character-design.md](../character-design.md) |
| Action 系统 | [../action-system.md](../action-system.md) |
