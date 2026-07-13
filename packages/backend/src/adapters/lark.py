"""Lark（飞书）适配器 - 通过 HTTP Webhook 接收事件

职责：
1. 暴露 POST /api/v1/lark/webhook 端点接收 Lark 事件推送
2. 处理 Lark URL 验证 challenge 握手
3. 处理 im.message.receive_v1 事件，转发至 MessageService
4. 通过 Lark Open API 向用户回推角色回复

设计要点：
- 凭证：通过环境变量 LARK_APP_ID / LARK_APP_SECRET 配置应用凭证
- 用户映射：Lark open_id -> (user_id="lark_{open_id}", platform="lark")
- 角色 ID：通过环境变量 LARK_DEFAULT_CHARACTER_ID 指定默认对话角色
- tenant_access_token 带过期缓存（默认 7000s，留 200s 缓冲），避免每次调用都申请
- LLM 客户端通过 `from src.runtime import get_llm, get_prompts` 延迟获取，避免循环导入
- HTTP 调用使用 httpx.AsyncClient
- 错误处理：捕获异常并记录日志，webhook 始终返回 200 避免 Lark 重试风暴

集成方式（在 main.py lifespan 中接入，本文件不修改 main.py）：
    from src.adapters import LarkAdapter

    lark_adapter = LarkAdapter()

    # 启动阶段（lifespan yield 之前）
    await lark_adapter.start()
    app.include_router(lark_adapter.router)

    # 关闭阶段（lifespan yield 之后）
    await lark_adapter.stop()
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from uuid import UUID

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from structlog import get_logger

from src.db.session import db
from src.messaging import MessageService

logger = get_logger(__name__)

# Lark Open API 基础地址
LARK_OPEN_BASE = "https://open.feishu.cn/open-apis"
LARK_TOKEN_URL = f"{LARK_OPEN_BASE}/auth/v3/tenant_access_token/internal"
LARK_SEND_MESSAGE_URL = f"{LARK_OPEN_BASE}/im/v1/messages"

# tenant_access_token 缓冲时间（秒），提前刷新避免边界过期
_TOKEN_REFRESH_BUFFER = 200


def _get_default_character_id() -> UUID | None:
    """从环境变量读取默认对话角色 ID

    Returns:
        角色 UUID；未配置或格式非法时返回 None
    """
    raw = os.environ.get("LARK_DEFAULT_CHARACTER_ID")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        logger.warning("lark_default_character_id_invalid", value=raw)
        return None


def _get_llm_globals() -> tuple[object | None, object | None]:
    """延迟获取全局 LLM 客户端与 Prompt 模板（避免循环导入）

    Returns:
        (llm, prompts) 元组，启动期可能为 (None, None)
    """
    from src.runtime import get_llm, get_prompts

    return get_llm(), get_prompts()


def _extract_text_content(content: str | None) -> str:
    """从 Lark 消息 content 字段提取纯文本

    Lark 文本消息的 content 形如 '{"text":"hello"}'（JSON 字符串）。

    Args:
        content: Lark event.message.content 原始字符串

    Returns:
        提取出的纯文本；无法解析时返回空字符串
    """
    if not content:
        return ""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(data, dict):
        text = data.get("text", "")
        if isinstance(text, str):
            return text.strip()
    return ""


class LarkAdapter:
    """Lark（飞书）平台适配器

    通过 HTTP Webhook 接收 Lark 事件，调用 MessageService 处理消息，
    并通过 Lark Open API 回推回复。
    """

    def __init__(self) -> None:
        self.router = APIRouter()
        self.router.post("/api/v1/lark/webhook")(self._webhook_endpoint)

        self._app_id = os.environ.get("LARK_APP_ID")
        self._app_secret = os.environ.get("LARK_APP_SECRET")

        # tenant_access_token 缓存
        self._token: str | None = None
        self._token_expire_at: float = 0.0
        self._token_lock = asyncio.Lock()

        self._client: httpx.AsyncClient | None = None
        self._running = False

    async def start(self) -> None:
        """启动适配器：初始化 httpx 客户端并校验凭证"""
        self._client = httpx.AsyncClient(timeout=30.0)

        if not self._app_id or not self._app_secret:
            logger.warning(
                "lark_credentials_not_configured",
                hint="Set LARK_APP_ID and LARK_APP_SECRET env vars to enable Lark integration",
            )

        self._running = True
        logger.info(
            "lark_adapter_started",
            endpoint="/api/v1/lark/webhook",
            credentials_configured=bool(self._app_id and self._app_secret),
            default_character_id=str(_get_default_character_id())
            if _get_default_character_id()
            else None,
        )

    async def stop(self) -> None:
        """停止适配器：关闭 httpx 客户端"""
        self._running = False
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:
                logger.warning("lark_http_client_close_failed", error=str(e))
            self._client = None
        logger.info("lark_adapter_stopped")

    async def get_tenant_access_token(self) -> str | None:
        """获取 Lark tenant_access_token（带缓存）

        调用 Lark Open API 申请 tenant_access_token，并按 expire 缓存。
        过期前 _TOKEN_REFRESH_BUFFER 秒提前刷新。

        Returns:
            tenant_access_token；凭证缺失或调用失败时返回 None
        """
        if not self._app_id or not self._app_secret:
            logger.warning("lark_get_token_no_credentials")
            return None

        # 缓存命中
        if self._token and time.time() < self._token_expire_at:
            return self._token

        async with self._token_lock:
            # double-check，避免并发重复申请
            if self._token and time.time() < self._token_expire_at:
                return self._token

            if self._client is None:
                logger.warning("lark_get_token_client_not_ready")
                return None

            try:
                resp = await self._client.post(
                    LARK_TOKEN_URL,
                    json={"app_id": self._app_id, "app_secret": self._app_secret},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(
                    "lark_get_token_failed",
                    error=str(e),
                    exc_info=True,
                )
                return None

            if data.get("code") != 0:
                logger.error(
                    "lark_get_token_api_error",
                    code=data.get("code"),
                    msg=data.get("msg"),
                )
                return None

            self._token = data.get("tenant_access_token")
            expire = data.get("expire", 7200)
            self._token_expire_at = time.time() + max(expire - _TOKEN_REFRESH_BUFFER, 60)
            logger.info(
                "lark_token_acquired",
                expire_in=int(self._token_expire_at - time.time()),
            )
            return self._token

    async def send_message(self, open_id: str, content: str) -> bool:
        """通过 Lark Open API 向指定用户发送文本消息

        Args:
            open_id: 接收者 open_id
            content: 纯文本消息内容

        Returns:
            True 表示发送成功，False 表示失败
        """
        token = await self.get_tenant_access_token()
        if not token:
            logger.error("lark_send_no_token", open_id=open_id)
            return False

        if self._client is None:
            logger.error("lark_send_client_not_ready", open_id=open_id)
            return False

        # Lark 消息 content 需为 JSON 字符串
        payload = {
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": content}, ensure_ascii=False),
        }

        try:
            resp = await self._client.post(
                LARK_SEND_MESSAGE_URL,
                params={"receive_id_type": "open_id"},
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(
                "lark_send_failed",
                open_id=open_id,
                error=str(e),
                exc_info=True,
            )
            return False

        if data.get("code") != 0:
            logger.error(
                "lark_send_api_error",
                open_id=open_id,
                code=data.get("code"),
                msg=data.get("msg"),
            )
            return False

        logger.info(
            "lark_message_sent",
            open_id=open_id,
            message_length=len(content),
        )
        return True

    async def _webhook_endpoint(self, request: Request) -> JSONResponse:
        """Lark 事件 Webhook 入口端点

        处理：
        1. URL 验证（type=url_verification）：回显 challenge
        2. im.message.receive_v1 事件：转发至 MessageService 并回推回复

        始终返回 200，错误信息放在 body 中，避免 Lark 端重试风暴。
        """
        try:
            body = await request.json()
        except Exception as e:
            logger.warning("lark_webhook_invalid_json", error=str(e))
            return JSONResponse({"error": "invalid json"}, status_code=400)

        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

        # === URL 验证握手 ===
        if body.get("type") == "url_verification":
            challenge = body.get("challenge", "")
            logger.info("lark_url_verification", challenge=challenge[:32])
            return JSONResponse({"challenge": challenge})

        # === 事件分发 ===
        header = body.get("header") or {}
        event_type = header.get("event_type")

        if event_type == "im.message.receive_v1":
            await self._handle_message_event(body)
        else:
            logger.debug("lark_event_ignored", event_type=event_type)

        # 默认 ACK，避免 Lark 重试
        return JSONResponse({"code": 0, "msg": "ok"})

    async def _handle_message_event(self, event_payload: dict) -> None:
        """处理 im.message.receive_v1 事件

        流程：
        1. 从 event.sender.sender_id.open_id 提取用户标识
        2. 从 event.message.content 提取消息文本
        3. 调用 MessageService.handle_user_message 生成角色回复
        4. 通过 send_message 回推回复

        Args:
            event_payload: Lark 事件完整载荷（含 header / event）
        """
        event = event_payload.get("event") or {}
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}
        open_id = sender_id.get("open_id")

        message = event.get("message") or {}
        msg_type = message.get("message_type")
        content_raw = message.get("content")
        message_id = message.get("message_id")

        logger.info(
            "lark_message_received",
            open_id=open_id,
            message_type=msg_type,
            message_id=message_id,
        )

        # 仅处理文本消息（其他类型如图片/富文本后续扩展）
        if msg_type != "text":
            logger.info("lark_non_text_message_skipped", message_type=msg_type, open_id=open_id)
            return

        text = _extract_text_content(content_raw)
        if not text:
            logger.info("lark_empty_message_skipped", open_id=open_id)
            return

        # 映射到内部用户标识
        if not open_id:
            logger.warning("lark_missing_open_id")
            return
        internal_user_id = f"lark_{open_id}"

        # 获取默认角色 ID
        character_id = _get_default_character_id()
        if character_id is None:
            logger.warning(
                "lark_default_character_not_configured",
                hint="Set LARK_DEFAULT_CHARACTER_ID env var to enable Lark routing",
            )
            await self.send_message(open_id, "（机器人尚未配置对话角色，请联系管理员）")
            return

        # 获取 LLM 全局实例
        llm_client, prompts_obj = _get_llm_globals()
        if llm_client is None or prompts_obj is None:
            logger.warning("lark_llm_not_ready")
            await self.send_message(open_id, "（服务正在启动中，请稍后再试）")
            return

        # 调用 MessageService 处理用户消息
        try:
            async with db.session() as session:
                svc = MessageService(
                    session=session,
                    llm=llm_client,  # type: ignore[arg-type]
                    prompts=prompts_obj,  # type: ignore[arg-type]
                )
                result = await svc.handle_user_message(
                    character_id=character_id,
                    user_id=internal_user_id,
                    platform="lark",
                    content=text,
                )
        except Exception as e:
            logger.error(
                "lark_message_handle_failed",
                open_id=open_id,
                error=str(e),
                exc_info=True,
            )
            await self.send_message(open_id, "（消息处理失败，请稍后再试）")
            return

        # 回推角色回复
        reply_text = result.get("content", "")
        if not reply_text:
            logger.warning("lark_empty_reply", open_id=open_id)
            return

        await self.send_message(open_id, reply_text)
