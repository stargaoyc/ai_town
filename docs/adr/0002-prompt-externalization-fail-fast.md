# 0002 - Prompt 全量外置 YAML，缺失即启动失败

- 状态：Accepted
- 日期：2026-08-23
- 关联：docs/design-improvement-and-fixes.md P0-6、AGENTS.md §3.3

## 背景（Context）

审查时发现 9 处 LLM Prompt 内嵌在 Python 源码里（决策追加段、群聊判定、
分享文案、日记、反思等），且 `prompts.py` 在 YAML 缺失时**静默回退**内置
模板。后果：

1. 调整人设/语气需要改代码发版；
2. YAML 与内置模板构成双真相源，改了 YAML 不生效或被兜底覆盖都难以发现;
3. Prompt 迭代无法与代码版本解耦评审。

## 决策（Decision）

**全部 Prompt 外置到 `configs/prompts/*.yaml`；`PromptTemplates` 启动加载，
任何模板文件缺失即抛异常终止启动（fail-fast），不设内置兜底。**

- 模板渲染参数缺失同样 fail-fast（format KeyError 直接暴露）。
- Prompt 变更走配置提交，与代码版本解耦；`configs/prompts/` 是唯一真相源。

## 备选方案（Alternatives）

| 方案 | 否决原因 |
|------|----------|
| 内置默认 + 可选覆盖 | 双真相源是本次修复的起因；静默回退使「YAML 被误删」不可见 |
| 数据库存 Prompt | 引入第三配置真相源；无法 code review、无法随仓库回滚 |
| 仅开发模式允许回退 | 生产/开发行为分叉，「测试没问题上线坏」的经典路径 |

## 后果（Consequences）

**正面**

- 单一真相源：Prompt 行为完全由 `configs/prompts/` 目录内容决定。
- 配置错误在启动瞬间暴露，而不是运行期静默用过期模板。

**负面 / 义务**

- 部署包必须包含 `configs/prompts/`（compose 已挂载）。
- 新增 Prompt 场景需要同时新增 YAML 文件，否则启动失败——这是有意为之的约束。
