"""Prompt 模板 - 全部外置于 configs/prompts/*.yaml

单一真相源：所有 LLM Prompt 模板只存在于 YAML 文件中，
代码内不保留任何内置兜底模板。启动时校验必需模板，缺失即失败（fail-fast）。
"""

from pathlib import Path

import yaml
from structlog import get_logger

logger = get_logger(__name__)

# 启动必需的模板集合：缺失任何一个都拒绝启动，
# 防止 YAML 被误删后系统用过期逻辑静默运行
REQUIRED_TEMPLATES = frozenset(
    {
        "chat",
        "decision",
        "reflection",
        "safety",
        "chat_with",
        "decision_tools",
        "decision_react",
        "group_reply",
        "context_compress",
        "share_event",
        "share_routine",
        "memory_score",
        "diary",
        "person_memory",
    }
)


def _find_prompts_dir() -> Path:
    """向上逐级查找 configs/prompts 目录（本地仓库与容器布局深度不同）"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "configs" / "prompts"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"configs/prompts not found in any parent of {here}; 容器部署需挂载 ./configs:/app/configs")


class PromptTemplates:
    """Prompt 模板管理器"""

    def __init__(self, config_dir: Path | None = None) -> None:
        """初始化 Prompt 模板管理器

        Args:
            config_dir: 配置文件目录，默认为 configs/prompts
        """
        self.config_dir = config_dir or _find_prompts_dir()
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
            raise RuntimeError(
                f"Prompt 配置目录不存在: {self.config_dir}。"
                f"所有 Prompt 模板必须外置于该目录（见 docs/rules/prompt-style.md）"
            )

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

        missing = REQUIRED_TEMPLATES - set(self.templates)
        if missing:
            raise RuntimeError(
                f"缺少必需的 Prompt 模板: {sorted(missing)}。请在 {self.config_dir} 下补齐对应 YAML 文件"
            )

    def get(self, name: str, default: str | None = None) -> str:
        """获取模板

        Args:
            name: 模板名称
            default: 显式传入的回退值；未找到且未传时抛出 KeyError

        Returns:
            模板字符串

        Raises:
            KeyError: 模板不存在且未提供 default
        """
        if name in self.templates:
            return self.templates[name]
        if default is not None:
            return default
        raise KeyError(f"Prompt 模板不存在: {name}")

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
