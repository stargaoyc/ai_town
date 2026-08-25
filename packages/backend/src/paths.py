"""运行时路径解析 - 兼容仓库布局与容器布局

仓库布局：packages/backend/src/...，configs 在仓库根；
容器布局：/app/src/...，configs 由 compose 只读挂载在 /app/configs。
两种布局的公共锚点是「向上首个包含 configs/ 的祖先目录即项目根」，
据此定位，避免 parents[N] 层级硬编码随部署形态漂移。
"""

from pathlib import Path


def find_project_root() -> Path:
    """从当前文件向上查找首个包含 configs/ 目录的祖先（即项目根）

    Raises:
        FileNotFoundError: 所有祖先均无 configs/（配置挂载缺失，fail-fast）
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "configs").is_dir():
            return parent
    raise FileNotFoundError(
        "project root not found: no ancestor contains configs/ "
        "(container deployments must mount ./configs at /app/configs)"
    )
