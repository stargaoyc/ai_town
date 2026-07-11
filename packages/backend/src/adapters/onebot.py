"""OneBot v12 适配器 - QQ 机器人接入（反向 WebSocket）

职责：
1. 作为 WebSocket 服务端接收 OneBot v12 实现（NapCat / Lagrange 等）的反向连接
2. 解析 OneBot v12 事件（消息 / 元事件 / 心跳），转发消息至 MessageService
3. 通过 OneBot send_message action 向用户回推角色回复

设计要点：
- 反向 WebSocket：OneBot 实现主动连接本服务端（endpoint: /ws/onebot/v12）
- 用户映射：OneBot user_id -> (user_id="qq_{user_id}", platform="qq")
- 角色 ID：通过环境变量 ONEBOT_DEFAULT_CHARACTER_ID 指定默认对话角色
  （多角色路由策略可后续扩展，例如解析消息前缀或维护群-角色映射表）
- LLM 客户端通过 `from src.main import llm, prompts` 延迟获取，避免循环导入
- 错误处理：捕获异常并记录日志，不中断连接

集成方式（在 main.py lifespan 中接入，本文件不修改 main.py）：
    from src.adapters import OneBotAdapter

    onebot_adapter = OneBotAdapter()

    # 启动阶段（lifespan yield 之前）
    await onebot_adapter.start()
    app.include_router(onebot_adapter.router)

    # 关闭阶段（lifespan yield 之后）
    await onebot_adapter.stop()
"""
from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from structlog import get_logger

from src.db.session import db
from src.messaging import MessageService

logger = get_logger(__name__)


def _get_default_character_id() -> UUID | None:
    """从配置读取默认对话角色 ID

    Returns:
        角色 UUID；未配置或格式非法时返回 None
    """
    from src.config import settings

    raw = settings.onebot_default_character_id
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        logger.warning(
            "onebot_default_character_id_invalid",
            value=raw,
        )
        return None


def _get_llm_globals() -> tuple[object | None, object | None]:
    """延迟获取全局 LLM 客户端与 Prompt 模板（避免循环导入）

    Returns:
        (llm, prompts) 元组，启动期可能为 (None, None)
    """
    from src.main import llm, prompts  # type: ignore

    return llm, prompts


def _extract_text(event: dict) -> str:
    """从 OneBot v12 消息事件中提取纯文本

    优先使用 raw_message（OneBot v12 规范定义的纯文本表示）；
    若缺失则尝试从 message 段数组中拼接 text 段。

    Args:
        event: OneBot v12 事件字典

    Returns:
        提取出的纯文本；无法提取时返回空字符串
    """
    raw_message = event.get("raw_message")
    if isinstance(raw_message, str) and raw_message.strip():
        return raw_message.strip()

    message = event.get("message")
    if isinstance(message, list):
        parts: list[str] = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "text":
                data = seg.get("data") or {}
                text = data.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()

    return ""


class OneBotAdapter:
    """OneBot v12 反向 WebSocket 适配器

    OneBot 实现（NapCat / Lagrange 等）作为客户端主动连接本服务端，
    本适配器在 /ws/onebot/v12 端点接受连接并处理事件。
    """

    def __init__(self) -> None:
        self.router = APIRouter()
        self.router.websocket("/ws/onebot/v12")(self._ws_endpoint)

        # 活跃连接集合（用于广播与生命周期管理）
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._running = False

    async def start(self) -> None:
        """启动适配器（标记运行状态，路由由 FastAPI 自动接管）"""
        self._running = True
        logger.info(
            "onebot_adapter_started",
            endpoint="/ws/onebot/v12",
            default_character_id=str(_get_default_character_id())
            if _get_default_character_id()
            else None,
        )

    async def stop(self) -> None:
        """停止适配器，关闭所有 OneBot 连接"""
        self._running = False

        async with self._lock:
            conns = list(self._connections)
            self._connections.clear()

        for ws in conns:
            try:
                if ws.client_state != WebSocketState.DISCONNECTED:
                    await ws.close(code=1001, reason="adapter stopping")
            except Exception as e:
                logger.warning("onebot_conn_close_failed", error=str(e))

        logger.info("onebot_adapter_stopped", closed=len(conns))

    async def _register(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.add(websocket)

    async def _unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def _ws_endpoint(self, websocket: WebSocket) -> None:
        """OneBot v12 反向 WebSocket 入口端点

        协议：OneBot 实现连接后逐条推送事件 JSON（文本帧），
        本端点解析事件并分发到对应处理器。
        """
        await websocket.accept()
        await self._register(websocket)
        logger.info("onebot_client_connected", total_connections=len(self._connections))

        try:
            while True:
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    logger.info("onebot_client_disconnected")
                    break

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("onebot_invalid_json", raw=raw[:200])
                    continue

                if not isinstance(event, dict):
                    logger.warning("onebot_event_not_dict", event=event)
                    continue

                try:
                    await self.handle_event(event, websocket)
                except Exception as e:
                    # 单条事件处理失败不影响后续事件
                    logger.error(
                        "onebot_event_handle_failed",
                        error=str(e),
                        exc_info=True,
                        event_type=event.get("type"),
                    )
        except WebSocketDisconnect:
            logger.info("onebot_client_disconnected_outer")
        except Exception as e:
            logger.error("onebot_ws_unexpected_error", error=str(e), exc_info=True)
        finally:
            await self._unregister(websocket)
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.close(code=1000, reason="closing")
            except Exception:
                pass

    async def handle_event(self, event: dict, onebot_ws: WebSocket) -> None:
        """分发 OneBot 事件到对应处理器（兼容 OneBot 11 和 v12）

        OneBot 11 使用 post_type，OneBot v12 使用 type。

        Args:
            event: OneBot 事件字典
            onebot_ws: 该事件来源的 WebSocket 连接（用于回推消息）
        """
        # 兼容 OneBot 11 (post_type) 和 OneBot v12 (type)
        event_type = event.get("type") or event.get("post_type")

        if event_type == "message":
            await self._handle_message_event(event, onebot_ws)
        elif event_type == "meta_event":
            await self._handle_meta_event(event)
        elif event_type == "notice":
            logger.debug("onebot_notice_event_ignored", detail_type=event.get("detail_type"))
        elif event_type == "request":
            logger.debug("onebot_request_event_ignored", detail_type=event.get("detail_type"))
        else:
            logger.debug("onebot_unknown_event", event_type=event_type)

    async def _handle_message_event(self, event: dict, onebot_ws: WebSocket) -> None:
        """处理 OneBot 消息事件（私聊 / 群聊），兼容 OneBot 11 和 v12

        流程：
        1. 提取 user_id / group_id / message_type / raw_message
        2. 映射到内部用户标识 (qq_{user_id}, platform=qq)
        3. 调用 MessageService.handle_user_message 生成角色回复
        4. 通过 send_message 回推回复
        """
        # 兼容 OneBot v12 (detail_type) 和 OneBot 11 (message_type)
        detail_type = event.get("detail_type") or event.get("message_type")
        user_id = event.get("user_id")
        group_id = event.get("group_id")
        raw_message = _extract_text(event)

        logger.info(
            "onebot_message_received",
            detail_type=detail_type,
            user_id=user_id,
            group_id=group_id,
            raw_message=raw_message[:100],
        )

        if not raw_message:
            logger.info("onebot_empty_message_skipped", user_id=user_id, group_id=group_id)
            return

        # 映射到内部用户标识
        internal_user_id = f"qq_{user_id}" if user_id is not None else "qq_unknown"

        # 获取默认角色 ID
        character_id = _get_default_character_id()
        if character_id is None:
            logger.warning(
                "onebot_default_character_not_configured",
                hint="Set ONEBOT_DEFAULT_CHARACTER_ID env var to enable QQ routing",
            )
            try:
                await self.send_message(
                    onebot_ws,
                    event_type=detail_type or "private",
                    user_id=user_id,
                    group_id=group_id,
                    message="（机器人尚未配置对话角色，请联系管理员）",
                )
            except Exception as e:
                logger.error("onebot_send_config_error_failed", error=str(e))
            return

        # 获取 LLM 全局实例
        llm_client, prompts_obj = _get_llm_globals()
        if llm_client is None or prompts_obj is None:
            logger.warning("onebot_llm_not_ready")
            try:
                await self.send_message(
                    onebot_ws,
                    event_type=detail_type or "private",
                    user_id=user_id,
                    group_id=group_id,
                    message="（服务正在启动中，请稍后再试）",
                )
            except Exception as e:
                logger.error("onebot_send_warmup_error_failed", error=str(e))
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
                    platform="qq",
                    content=raw_message,
                )
        except Exception as e:
            logger.error(
                "onebot_message_handle_failed",
                user_id=internal_user_id,
                error=str(e),
                exc_info=True,
            )
            try:
                await self.send_message(
                    onebot_ws,
                    event_type=detail_type or "private",
                    user_id=user_id,
                    group_id=group_id,
                    message="（消息处理失败，请稍后再试）",
                )
            except Exception as send_err:
                logger.error("onebot_send_error_failed", error=str(send_err))
            return

        # 回推角色回复
        reply_text = result.get("content", "")
        if not reply_text:
            logger.warning("onebot_empty_reply", user_id=internal_user_id)
            return

        try:
            await self.send_message(
                onebot_ws,
                event_type=detail_type or "private",
                user_id=user_id,
                group_id=group_id,
                message=reply_text,
            )
        except Exception as e:
            logger.error(
                "onebot_send_reply_failed",
                user_id=internal_user_id,
                error=str(e),
                exc_info=True,
            )

    async def _handle_meta_event(self, event: dict) -> None:
        """处理 OneBot v12 元事件（心跳 / 生命周期）

        仅记录日志，不做业务处理。
        """
        detail_type = event.get("detail_type")
        if detail_type == "heartbeat":
            logger.debug(
                "onebot_heartbeat",
                status=event.get("status"),
                interval=event.get("interval"),
            )
        elif detail_type == "lifecycle":
            logger.info(
                "onebot_lifecycle",
                sub_type=event.get("sub_type"),
            )
        else:
            logger.debug("onebot_meta_event", detail_type=detail_type)

    async def send_message(
        self,
        onebot_ws: WebSocket,
        event_type: str,
        user_id: str | int | None,
        group_id: str | int | None,
        message: str,
    ) -> None:
        """通过 OneBot v12 send_message action 回推消息

        构造 OneBot v12 标准的 send_message action，通过传入的 WebSocket 连接
        发送给 OneBot 实现，由其实际投递到 QQ 私聊/群聊。

        Args:
            onebot_ws: OneBot 实现的 WebSocket 连接
            event_type: 目标会话类型（"private" 或 "group"）
            user_id: OneBot 用户 ID（私聊必填）
            group_id: OneBot 群 ID（群聊必填）
            message: 待发送的纯文本消息
        """
        # 根据 OneBot v12 规范组装 action
        params: dict = {
            "detail_type": event_type,
            "message": [{"type": "text", "data": {"text": message}}],
        }
        if event_type == "group":
            if group_id is None:
                logger.warning("onebot_send_missing_group_id", user_id=user_id)
                return
            params["group_id"] = group_id
        else:
            if user_id is None:
                logger.warning("onebot_send_missing_user_id", group_id=group_id)
                return
            params["user_id"] = user_id

        action = {
            "action": "send_message",
            "params": params,
        }

        try:
            await onebot_ws.send_text(json.dumps(action, ensure_ascii=False))
            logger.info(
                "onebot_message_sent",
                event_type=event_type,
                user_id=user_id,
                group_id=group_id,
                message_length=len(message),
            )
        except Exception as e:
            logger.error(
                "onebot_send_failed",
                event_type=event_type,
                user_id=user_id,
                group_id=group_id,
                error=str(e),
                exc_info=True,
            )
            raise
