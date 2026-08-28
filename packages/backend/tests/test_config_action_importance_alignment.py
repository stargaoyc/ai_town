"""action_base_importance 与真实 Action ID 的一致性守卫

审查 §5.3 发现：该字典曾以通用类别名（rest/drink/work/chat/...）为键，
而 decision.action 取的是具体 id（relax/eat_at_home/chat_with/...），
14 个 action 中仅 5 个能命中，分级形同虚设。

新增 Action 而忘记补配置、或配置键拼错都会静默落到默认值 5，
因此用测试把「配置键集合 ⊆ Action ID 集合」钉死。
"""

from typing import Any, cast

from redis.asyncio import Redis

from src.actions import ActionRegistry, register_all
from src.config import settings
from src.modules.town.loader import SceneLoader
from src.paths import find_project_root


class _FakeRedis:
    """SceneLoader 构造用替身：load_configs_sync 不触碰 Redis，仅满足类型"""


def _real_action_ids() -> set[str]:
    """注册全部内置 Action 后的真实 ID 集合（与生产同源）

    走真实 configs/scenes.yaml：带 scene_tags 的 Action 在注册期就需要
    SceneLoader 解析标签，用替身会让注册链与生产不一致。
    """
    loader = SceneLoader(cast(Redis, cast(Any, _FakeRedis())))
    root = find_project_root()
    loader.load_configs_sync(root / "configs" / "scenes.yaml", root / "configs" / "world-map.yaml")
    registry = ActionRegistry(scene_loader=loader)
    register_all(registry)
    return {a.id for a in registry.list_all()}


def test_action_base_importance_keys_match_action_ids() -> None:
    """每个键都必须对应一个真实 Action，否则是永不命中的死键"""
    known = _real_action_ids()
    dead = sorted(set(settings.action_base_importance) - known)
    assert not dead, f"action_base_importance 存在不存在的 action 键: {dead}"


def test_action_base_importance_covers_all_actions() -> None:
    """每个 Action 都应显式定级，避免新 Action 静默走默认值"""
    known = _real_action_ids()
    missing = sorted(known - set(settings.action_base_importance))
    assert not missing, f"以下 action 未在 action_base_importance 中定级: {missing}"


def test_action_base_importance_values_in_range() -> None:
    """分值须落在 importance 的合法域 [1,10]，否则 retention 分级会越界"""
    for action_id, value in settings.action_base_importance.items():
        assert 1 <= value <= 10, f"{action_id} 的 importance {value} 超出 [1,10]"


def test_emotion_boost_cannot_pin_into_permanent() -> None:
    """情绪加成不得把任何动作推入永久保留集合（审查 §5.3）

    加成上限默认低于永久保留阈值，否则高频动作（社交）在带情绪词时会
    被永久钉住、无法回收，是记忆膨胀的主因。需要「强情绪永久留存」时
    应显式上调 memory_emotion_boost_max_total，而非默认踩进这个坑。
    """
    boost = settings.action_emotion_importance_boost
    if boost <= 0:
        return
    permanent = settings.memory_retention_permanent_importance
    cap = settings.memory_emotion_boost_max_total
    assert cap < permanent, f"加成上限 {cap} 不应达到永久保留阈值 {permanent}：高频动作会被永久钉住"


def test_social_action_importance_below_permanent() -> None:
    """社交类基础分本身也须低于永久保留阈值（P1-7）"""
    permanent = settings.memory_retention_permanent_importance
    for action_id in ("chat_with", "group_activity"):
        base = settings.action_base_importance.get(action_id, 5)
        assert base < permanent, f"{action_id} 基础分 {base} 不应达到永久保留阈值 {permanent}"
