"""创意生成工具 - 图片/视频生成（生成→QQ 链路的源头）

角色通过 ReAct 调用 draw_image / generate_video，内部走 LLMClient
（图片 MODEL_IMAGE / 视频 MODEL_VIDEO）。图片同步返回 URL；
视频为异步任务：调用立即返回受理回执，轮询与产物落库在进程后台
任务中完成（P0-1：同步轮询 1-10 分钟会占死角色 Tick 信号量槽位）。

限制：
- 只读工具，不修改任何状态
- LLM 客户端经 runtime.get_llm() 延迟获取，未初始化时返回失败
- 成本盲区（round-3 M18）：图片/视频生成 API 不返回 token 用量，
  无法按 token 口径入账——这是 API 契约限制而非遗漏；
  MEDIA_GENERATION_TOTAL（定义于 observability/metrics.py）以调用量计数兜住可观测性（费用仍不可估）
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from src.observability.metrics import MEDIA_GENERATION_TOTAL
from src.runtime import get_llm

logger = structlog.get_logger()

_RATIOS = {"1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"}


def _snap_frames(frames: int) -> int:
    """帧数约束为 8n+1（agnes-video 要求），最小 25"""
    if frames < 25:
        return 25
    snapped = ((frames - 1 + 7) // 8) * 8 + 1
    return max(25, snapped)


async def _finalize_video(character_id: str, prompt: str, frames: int) -> None:
    """后台轮询视频任务并把产物写入角色记忆（P0-1 后台半场）

    在进程后台注册表中运行，不占用任何 Tick 槽位；完成后以
    source_type=action 的记忆沉淀视频 URL，供后续决策/对话引用。
    """
    from src.core.background import spawn_background

    llm = get_llm()
    if llm is None:
        return

    async def _run() -> None:
        try:
            url = await llm.generate_video(prompt=prompt, num_frames=frames)
        except Exception as exc:
            MEDIA_GENERATION_TOTAL.labels(tool="generate_video", outcome="failed").inc()
            logger.warning("generate_video_bg_failed", error=str(exc), character_id=character_id)
            return
        if not url.startswith("http"):
            MEDIA_GENERATION_TOTAL.labels(tool="generate_video", outcome="failed").inc()
            logger.warning("generate_video_bg_invalid_url", character_id=character_id)
            return

        MEDIA_GENERATION_TOTAL.labels(tool="generate_video", outcome="success").inc()
        logger.info("generate_video_bg_ok", character_id=character_id, frames=frames)

        from src.db.session import db
        from src.memory.episode_service import EpisodeService

        async with db.session() as session:
            from src.db.repositories import MemoryRepository

            service = EpisodeService(llm, MemoryRepository(session))
            await service.create_episode(
                character_id=UUID(character_id),
                content=f"[视频作品] 我生成了一段短视频：{prompt[:80]}……成片已就绪：{url}",
                action_id="media.generate_video",
                importance=6,
                character_name=None,
                reason="完成一个异步视频生成任务",
            )

    spawn_background(_run(), name=f"media.generate_video:{character_id}")


async def generate_video_clip(prompt: str, frames: int = 25, character_id: str = "") -> dict[str, Any]:
    """提交一段短视频生成任务（异步受理，立即返回）

    P0-1：此前在本函数内同步轮询直至完成（典型 1-3 分钟、上限约 10 分钟），
    期间占死当前角色的 Tick 信号量槽位——两个并发视频即可吃掉 20% 吞吐。
    现改为：校验后立即提交后台任务并返回受理回执；产物由后台半场
    写入角色记忆（_finalize_video）。

    Args:
        prompt: 视频内容的文字描述
        frames: 目标帧数（自动对齐到 8n+1，越大越长越慢）
        character_id: 发起角色 ID（registry 自动注入），用于产物记忆归属

    Returns:
        受理回执：{"success": True, "pending": True, "message": ..., ...}
    """
    llm = get_llm()
    if llm is None:
        return {"success": False, "error": "LLM 未初始化，无法生成视频"}

    snapped = _snap_frames(frames)
    if character_id:
        await _finalize_video(character_id, prompt, snapped)

    MEDIA_GENERATION_TOTAL.labels(tool="generate_video", outcome="submitted").inc()
    logger.info("generate_video_submitted", frames=snapped, character_id=character_id)
    return {
        "success": True,
        "pending": True,
        "message": f"视频生成任务已提交（约 {frames} 帧），完成后我会把成片分享出来",
        "prompt": prompt,
        "frames": snapped,
    }


async def draw_image(prompt: str, ratio: str = "1:1") -> dict[str, Any]:
    """根据文字描述生成一张图片

    Args:
        prompt: 画面描述（具体、含风格与氛围，越具体效果越好）
        ratio: 画面比例（1:1/3:4/4:3/16:9/9:16/2:3/3:2/21:9，默认 1:1）

    Returns:
        成功：{"success": True, "url": "<图片URL>", "cq_code": "[CQ:image,file=<URL>]",
               "prompt": prompt, "ratio": ratio}
        失败：{"success": False, "error": str}
    """
    llm = get_llm()
    if llm is None:
        return {"success": False, "error": "LLM 未初始化，无法生成图片"}

    if ratio not in _RATIOS:
        ratio = "1:1"

    try:
        url = await llm.generate_image(prompt=prompt, ratio=ratio)
    except Exception as exc:
        MEDIA_GENERATION_TOTAL.labels(tool="draw_image", outcome="failed").inc()
        logger.warning("draw_image_failed", error=str(exc))
        return {"success": False, "error": f"图片生成失败: {exc}"}

    # data URI 形式无法作为 CQ 图片 URL 直接发送，仅回传提示
    cq_code = f"[CQ:image,file={url}]" if url.startswith("http") else ""

    MEDIA_GENERATION_TOTAL.labels(tool="draw_image", outcome="success").inc()
    logger.info("draw_image_ok", ratio=ratio, has_cq=bool(cq_code))
    return {
        "success": True,
        "url": url,
        "cq_code": cq_code,
        "prompt": prompt,
        "ratio": ratio,
    }
