"""Prompt 模板 - 角色决策、反思、对话回复

模板从 YAML 文件加载（configs/prompts/*.yaml）
或使用默认模板
"""
from pathlib import Path

import yaml
from structlog import get_logger

logger = get_logger(__name__)


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

[对话上下文]
{context}

[对话历史]
{history}

[输出格式]
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
        self._load_templates()

    def _load_templates(self) -> None:
        """从 YAML 文件加载模板"""
        if not self.config_dir.exists():
            logger.warning("prompt_config_dir_not_found", path=str(self.config_dir))
            return

        for yaml_file in self.config_dir.glob("*.yaml"):
            try:
                with yaml_file.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if data and isinstance(data, dict) and data.get("name") and data.get("template"):
                        self.templates[data["name"]] = data["template"]
                        logger.debug("template_loaded", name=data["name"], file=str(yaml_file))
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

    def reload(self) -> None:
        """重新加载模板（用于热更新）"""
        self.templates.clear()
        self._load_templates()
        logger.info("templates_reloaded", count=len(self.templates))