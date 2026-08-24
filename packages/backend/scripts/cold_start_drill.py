"""冷启动恢复演练 - 验证「清空 Redis -> PG 回灌」端到端闭环

模拟真实事故：删除世界状态与角色实时状态键，执行与 main.py 启动路径
完全一致的 rehydrate_states()，校验：
  1. world:state 从最新 world_snapshots 恢复（tick_id/weather 一致）
  2. 全部角色 char:{id}:state 从 PG 镜像回灌（数量一致）

⚠️ 会清除本机 Redis 的世界/角色状态键并从 PG 镜像重建——
镜像存在最长一个对账周期（10 分钟）的滞后，属演练预期语义。

用法：
    cd packages/backend
    uv run python scripts/cold_start_drill.py            # 演练世界+角色
    uv run python scripts/cold_start_drill.py --world-only  # 仅世界状态
"""

from __future__ import annotations

import asyncio
import sys

from redis.asyncio import Redis

from src.config import settings
from src.core.rehydration import rehydrate_states
from src.db.models import CharacterState
from src.db.repositories import CharacterRepository, WorldSnapshotRepository
from src.db.session import db

WORLD_KEYS = [
    "world:state",
    "world:events:baseline",
    *(f"world:state:{dim}" for dim in ("time", "weather", "scenes", "resources", "events")),
]

_results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    _results.append((ok, label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


async def main(include_characters: bool = True) -> int:
    r = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        print("=== 冷启动恢复演练 ===")

        # 0. 前置条件：PG 必须有快照可供回灌
        async with db.session() as session:
            snapshot = await WorldSnapshotRepository(session).get_latest()
            states = list(await CharacterRepository(session).get_all_states())
        if snapshot is None:
            print("SKIP：PG 无 world_snapshots 记录，先启动世界引擎产生快照后再演练")
            return 2
        print(
            f"PG 基线：snapshot tick_id={snapshot.tick_id} weather={snapshot.weather} "
            f"角色镜像={len(states)} 条"
        )

        # 1. 记录演练前状态
        pre_world = await r.hgetall("world:state")
        pre_char_keys = [k async for k in r.scan_iter(match="char:*:state")]
        print(f"演练前：world:state={'有' if pre_world else '无'} tick_id={pre_world.get('tick_id', '-')}，"
              f"角色键={len(pre_char_keys)} 个")

        # 2. 模拟冷启动：清除世界与角色状态键
        await r.delete(*WORLD_KEYS)
        removed_chars = 0
        if include_characters:
            for key in pre_char_keys:
                await r.delete(key)
                removed_chars += 1
        print(f"已清除世界键 {len(WORLD_KEYS)} 个、角色键 {removed_chars} 个")

        # 3. 执行回灌（与 main.py lifespan 启动路径一致）
        await rehydrate_states(r)

        # 4. 校验
        post_world = await r.hgetall("world:state")
        check(bool(post_world), "world:state 已恢复")
        check(
            post_world.get("tick_id") == str(snapshot.tick_id),
            f"tick_id 一致（期望 {snapshot.tick_id}，实际 {post_world.get('tick_id')}）",
        )
        expected_weather = snapshot.weather or "sunny"
        check(
            post_world.get("weather") == expected_weather,
            f"weather 一致（期望 {expected_weather}，实际 {post_world.get('weather')}）",
        )

        post_char_keys = [k async for k in r.scan_iter(match="char:*:state")]
        if include_characters:
            check(
                len(post_char_keys) == len(states),
                f"角色键全部回灌（期望 {len(states)}，实际 {len(post_char_keys)}）",
            )
            spot = await _spot_check_character(r, states)
            check(spot, "抽查角色字段与 PG 镜像一致")
        else:
            print("跳过角色层校验（--world-only）")

        failed = [label for ok, label in _results if not ok]
        print(f"\n=== 结果：{'PASS' if not failed else 'FAIL'}（{len(_results) - len(failed)}/{len(_results)} 通过）===")
        return 0 if not failed else 1
    finally:
        await r.aclose()


async def _spot_check_character(r: Redis, states: list[CharacterState]) -> bool:
    """抽查第一个角色的 location/mood 与 PG 镜像一致"""
    from src.core.state_codec import encode_state_mapping

    if not states:
        return True
    st = states[0]
    restored = await r.hgetall(f"char:{st.character_id}:state")
    expected = encode_state_mapping(
        {
            "location": st.location,
            "mood": st.mood,
            "money": st.money,
        }
    )
    for field, value in expected.items():
        if value and restored.get(field) != value:
            print(f"      字段不一致：{field} 期望 {value!r} 实际 {restored.get(field)!r}")
            return False
    return True


if __name__ == "__main__":
    code = asyncio.run(main(include_characters="--world-only" not in sys.argv))
    sys.exit(code)
