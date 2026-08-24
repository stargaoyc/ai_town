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
