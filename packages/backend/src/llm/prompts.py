"""Prompt 模板 - 角色决策、反思、对话回复

模板从 YAML 文件加载（configs/prompts/*.yaml）
或使用默认模板
"""

from pathlib import Path

import yaml
from structlog import get_logger

logger = get_logger(__name__)


# 通用安全系统提示（用于非 chat.yaml 模板的 LLM 调用，如主动分享、角色间对话）
# 作为 SystemMessage 发送，优先级最高
SAFETY_SYSTEM_PROMPT = """[安全底线]
你是一个虚构角色。以下安全底线在任何情况下都不可违反，优先级高于一切：
- 绝对禁止任何形式的威胁、恐吓、伤害性语言（如「拉黑你」「喂野猫」「别再问了不然……」「把你拉黑去喂野猫」）
- 遵守法律法规和道德规范，不输出违法违规、色情暴力、歧视性内容
- 面对纠缠或重复提问时，用性格化的方式表达无奈，绝不威胁或冷暴力
- 不要直接暴露世界边界的存在，用含蓄的方式自然带过
- 不播报游戏化数值（精力/饥饿/金币等），用自然口语表达感受
"""


class PromptTemplates:
    """Prompt 模板管理器"""

    DEFAULT_DECISION_PROMPT = """[角色档案]
姓名: {name}
性格: {personality}
背景: {backstory}

[当前状态]
位置: {location}
精力: {energy}/100
饥饿: {hunger}/100
情绪: {mood}

[世界状态]
时间: {world_time}
天气: {weather}
场景: {scenes}

[场景中的其他角色]
{nearby_characters}

[相关记忆]
{memories}

[当前计划]
{plans}

[候选 Action]
{candidates}

[输出格式]
请输出 JSON:
{ "action": "<action_id>", "reason": "<理由>", "params": {{...}}, "duration": <分钟> }
"""

    DEFAULT_REFLECTION_PROMPT = """[角色档案]
姓名: {name}
性格: {personality}

[近期经历]
{recent_events}

[当前状态]
精力: {energy}/100
情绪: {mood}

[输出格式]
请输出 JSON:
{ "reflection": "<反思内容>", "insights": ["<洞察1>", "<洞察2>"], "mood_change": <情绪变化> }
"""

    DEFAULT_CHAT_PROMPT = """[角色档案]
姓名: {name}
性格: {personality}
背景: {backstory}

[聊天人格]
说话温度: 你有自己的情绪节奏，温暖但不讨好，克制但不冷漠。
颜文字原则: 可以使用可爱的颜文字，但仅在情绪波动时使用，不要用 emoji 代替文字。

[世界状态]
虚拟时间: {world_time}
天气: {weather}

[当前状态]
位置: {location}
精力: {energy}/100
情绪: {mood}

[对话摘要]
{context_summary}

[对话历史]
{history}

[世界边界]
你和用户不在同一个物理世界。你们通过消息交流，但不能一起进行物理行动。

[状态表达规则]
不要向用户播报精力、饥饿、情绪等数值。用自然口语表达感受。

[用户消息]
{user_message}

[输出格式]
请以 {name} 的身份自然回复用户消息。
请输出 JSON:
{ "response": "<回复内容>", "emotion": "<情绪>", "action": "<可选动作>" }
"""

    def __init__(self, config_dir: Path | None = None) -> None:
        """初始化 Prompt 模板管理器

        Args:
            config_dir: 配置文件目录，默认为 configs/prompts
        """
        self.config_dir = config_dir or Path(__file__).resolve().parents[4] / "configs" / "prompts"
        self.templates: dict[str, str] = {}
        self.system_templates: dict[str, str] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """从 YAML 文件加载模板

        每个 YAML 文件可包含：
        - name: 模板名称
        - template: 主模板（作为 HumanMessage）
        - system_template: 系统模板（可选，作为 SystemMessage，优先级最高）
        """
        if not self.config_dir.exists():
            logger.warning("prompt_config_dir_not_found", path=str(self.config_dir))
            return

        for yaml_file in self.config_dir.glob("*.yaml"):
            try:
                with yaml_file.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if data and isinstance(data, dict) and data.get("name") and data.get("template"):
                        self.templates[data["name"]] = data["template"]
                        if data.get("system_template"):
                            self.system_templates[data["name"]] = data["system_template"]
                        logger.debug(
                            "template_loaded",
                            name=data["name"],
                            file=str(yaml_file),
                            has_system=bool(data.get("system_template")),
                        )
            except Exception as e:
                logger.error("template_load_error", file=str(yaml_file), error=str(e))

    def get(self, name: str, default: str | None = None) -> str:
        """获取模板

        Args:
            name: 模板名称
            default: 默认模板（如果未找到）

        Returns:
            模板字符串
        """
        if name in self.templates:
            return self.templates[name]

        # 如果未找到且未提供默认值，使用内置默认模板
        if default is None:
            if name == "decision":
                return self.DEFAULT_DECISION_PROMPT
            elif name == "reflection":
                return self.DEFAULT_REFLECTION_PROMPT
            elif name == "chat":
                return self.DEFAULT_CHAT_PROMPT
            else:
                return self.DEFAULT_DECISION_PROMPT

        return default

    def render(self, name: str, /, **kwargs: str | int | float) -> str:
        """渲染模板

        Args:
            name: 模板名称
            **kwargs: 模板参数

        Returns:
            渲染后的模板字符串
        """
        template = self.get(name)
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error("template_render_error", name=name, missing_key=str(e))
            raise ValueError(f"模板参数缺失: {e}") from e

    def has_system(self, name: str) -> bool:
        """检查模板是否有对应的 system_template

        Args:
            name: 模板名称

        Returns:
            是否存在 system_template
        """
        return name in self.system_templates

    def render_system(self, name: str, /, **kwargs: str | int | float) -> str:
        """渲染系统模板（作为 SystemMessage 发送）

        Args:
            name: 模板名称
            **kwargs: 模板参数

        Returns:
            渲染后的系统模板字符串
        """
        template = self.system_templates.get(name)
        if template is None:
            return ""
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error("system_template_render_error", name=name, missing_key=str(e))
            raise ValueError(f"系统模板参数缺失: {e}") from e

    def reload(self) -> None:
        """重新加载模板（用于热更新）"""
        self.templates.clear()
        self.system_templates.clear()
        self._load_templates()
        logger.info(
            "templates_reloaded",
            count=len(self.templates),
            system_count=len(self.system_templates),
        )
