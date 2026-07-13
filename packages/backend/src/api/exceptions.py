"""全局异常处理

统一错误响应格式，区分 HTTPException（透传）、ValueError/TypeError（400）、其他异常（500）。
内部异常不直接暴露 str(e)，返回通用错误消息 + trace_id 供排查。
"""

import uuid
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from structlog import get_logger

logger = get_logger(__name__)


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局异常处理器

    - HTTPException: 透传 status_code 和 detail
    - ValueError / TypeError: 返回 400
    - 其他异常: 返回 500，不暴露内部错误信息
    """
    trace_id = str(uuid.uuid4())

    if isinstance(exc, HTTPException):
        # HTTPException 透传，但统一格式
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "trace_id": trace_id,
            },
        )

    if isinstance(exc, (ValueError, TypeError)):
        logger.warning(
            "client_error",
            trace_id=trace_id,
            path=request.url.path,
            method=request.method,
            error=str(exc),
        )
        return JSONResponse(
            status_code=400,
            content={
                "detail": str(exc),
                "trace_id": trace_id,
                "error_code": "bad_request",
            },
        )

    # 未知异常：记录完整堆栈，返回通用错误消息
    logger.error(
        "unhandled_exception",
        trace_id=trace_id,
        path=request.url.path,
        method=request.method,
        error=str(exc),
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "trace_id": trace_id,
            "error_code": "internal_error",
        },
    )


def register_exception_handlers(app) -> None:
    """注册全局异常处理器到 FastAPI 应用"""
    app.add_exception_handler(Exception, global_exception_handler)
