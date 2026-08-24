"""创意生成工具 - 图片生成（生成→QQ 链路的源头）

角色通过 ReAct 调用 draw_image，内部走 LLMClient.generate_image
（MODEL_STRONG = agnes-image-2.1-flash），返回图片 URL 与可直接嵌入
QQ 回复的 CQ 码；出站净化由 OneBot 适配器统一处理。

限制：
- 只读工具，不修改任何状态
- LLM 客户端经 runtime.get_llm() 延迟获取，未初始化时返回失败
"""

from __future__ import annotations

from typing import Any

import structlog

from src.runtime import get_llm

logger = structlog.get_logger()

_RATIOS = {"1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"}


def _snap_frames(frames: int) -> int:
    """帧数约束为 8n+1（agnes-video 要求），最小 25"""
    if frames < 25:
        return 25
    snapped = ((frames - 1 + 7) // 8) * 8 + 1
    return max(25, snapped)


async def generate_video_clip(prompt: str, frames: int = 25) -> dict[str, Any]:
    """根据文字描述生成一段短视频

    ⚠️ 同步轮询直至生成完成，耗时约 1-3 分钟——会占用当前角色 Tick 槽位，
    其他角色不受影响。完成后返回视频 URL 与 CQ 码。

    Args:
        prompt: 视频内容的文字描述
        frames: 目标帧数（自动对齐到 8n+1，越大越长越慢）

    Returns:
        成功：{"success": True, "url": ..., "cq_code": "[CQ:video,file=<URL>]", ...}
    """
    llm = get_llm()
    if llm is None:
        return {"success": False, "error": "LLM 未初始化，无法生成视频"}

    snapped = _snap_frames(frames)
    try:
        url = await llm.generate_video(prompt=prompt, num_frames=snapped)
    except Exception as exc:
        logger.warning("generate_video_failed", error=str(exc))
        return {"success": False, "error": f"视频生成失败: {exc}"}

    if not url.startswith("http"):
        return {"success": False, "error": "video url invalid"}

    logger.info("generate_video_ok", frames=snapped)
    return {
        "success": True,
        "url": url,
        "cq_code": f"[CQ:video,file={url}]",
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
        logger.warning("draw_image_failed", error=str(exc))
        return {"success": False, "error": f"图片生成失败: {exc}"}

    # data URI 形式无法作为 CQ 图片 URL 直接发送，仅回传提示
    cq_code = f"[CQ:image,file={url}]" if url.startswith("http") else ""

    logger.info("draw_image_ok", ratio=ratio, has_cq=bool(cq_code))
    return {
        "success": True,
        "url": url,
        "cq_code": cq_code,
        "prompt": prompt,
        "ratio": ratio,
    }
