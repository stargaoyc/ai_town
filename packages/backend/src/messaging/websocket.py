"""WebSocket 适配器 - Web 客户端实时聊天

职责：
1. 管理活跃 WebSocket 连接（按 (user_id, character_id) 维度索引）
2. 提供向指定用户-角色对推送消息的能力（send_to_user）
3. 提供向某角色的所有在线用户广播消息的能力（broadcast，用于角色主动消息）
4. 提供 /ws/chat/{character_id} 端点，复用 MessageService 处理用户消息

设计要点：
- 线程安全使用 asyncio 原语（asyncio.Lock），不使用 threading
- WebSocketManager 为单例，main.py 实例化一次后全局复用
- LLM 客户端通过 `from src.runtime import get_llm, get_prompts` 获取（启动期为 None）
- 错误处理：捕获异常并回送 error JSON，不中断连接
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from structlog import get_logger

from src.db.session import db
from src.llm import LLMClient, PromptTemplates
from src.messaging import MessageService
from src.observability.tracing import trace_span

logger = get_logger(__name__)


class WebSocketManager:
    """WebSocket 连接管理器（单例）

    连接索引：{(user_id, character_id): WebSocket}
    - 同一 (user_id, character_id) 仅保留最新连接，旧连接被覆盖前会尝试关闭
    - broadcast(character_id) 会遍历所有匹配该角色的连接
    """

    _instance: WebSocketManager | None = None

    def __new__(cls) -> WebSocketManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        # 单例已初始化则跳过
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        # (user_id, character_id) -> WebSocket
        self._connections: dict[tuple[str, str], WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        character_id: str,
        *,
        subprotocol: str | None = None,
    ) -> None:
        """注册一条 WebSocket 连接

        - 先 accept，再写入连接表
        - 若同 (user_id, character_id) 已存在旧连接，尝试关闭旧连接（避免资源泄漏）
        - 客户端经 Sec-WebSocket-Protocol 携带 token 时，握手需回选子协议（RFC 6455），
          否则浏览器端构造器直接握手失败

        Args:
            websocket: FastAPI WebSocket 对象
            user_id: 用户标识
            character_id: 角色 ID（字符串形式 UUID）
            subprotocol: 握手需回选的子协议；None 表示不回选
        """
        await websocket.accept(subprotocol=subprotocol)

        old_ws: WebSocket | None = None
        async with self._lock:
            key = (user_id, character_id)
            old_ws = self._connections.get(key)
            self._connections[key] = websocket

        # 在锁外关闭旧连接，避免阻塞其他操作
        if old_ws is not None and old_ws is not websocket:
            try:
                if old_ws.client_state != WebSocketState.DISCONNECTED:
                    await old_ws.close(code=1000, reason="replaced by new connection")
            except Exception as e:
                logger.warning(
                    "ws_close_old_failed",
                    user_id=user_id,
                    character_id=character_id,
                    error=str(e),
                )

        logger.info(
            "ws_connected",
            user_id=user_id,
            character_id=character_id,
            total_connections=len(self._connections),
        )

    async def disconnect(self, user_id: str, character_id: str) -> None:
        """移除一条连接（若存在）"""
        async with self._lock:
            key = (user_id, character_id)
            removed = self._connections.pop(key, None)

        if removed is not None:
            logger.info(
                "ws_disconnected",
                user_id=user_id,
                character_id=character_id,
                total_connections=len(self._connections),
            )

    async def _evict_if_current(self, user_id: str, character_id: str, ws: WebSocket) -> bool:
        """仅当 key 对应的连接仍是失败时的那条 ws 才移除（R5-M4）

        发送失败/超时往往耗时较长，期间用户可能已重连并占据同一 key：
        按 key 盲删会把新建立的活连接一并踢掉。同一性比较保证只清理
        真正断开的那条连接。

        Returns:
            True 表示发生了移除，False 表示 key 已被其他连接占用（跳过）
        """
        async with self._lock:
            key = (user_id, character_id)
            if self._connections.get(key) is ws:
                del self._connections[key]
                return True
        return False

    @trace_span("message.push")
    async def send_to_user(
        self,
        user_id: str,
        character_id: str,
        message: dict[str, Any],
    ) -> bool:
        """向指定 (user_id, character_id) 推送 JSON 消息

        带 10s 发送超时（R4-M12）：半开浏览器连接会让无超时的 send_json
        无限挂起，拖死调用方（分享扇出/通知推送）。

        Args:
            user_id: 用户标识
            character_id: 角色 ID
            message: 待发送的字典（会被 JSON 序列化）

        Returns:
            True 表示发送成功，False 表示连接不存在、超时或发送失败
        """
        async with self._lock:
            ws = self._connections.get((user_id, character_id))

        if ws is None:
            return False

        try:
            await asyncio.wait_for(ws.send_json(message), timeout=_WS_SEND_TIMEOUT_SECONDS)
            return True
        except Exception as e:
            logger.warning(
                "ws_send_to_user_failed",
                user_id=user_id,
                character_id=character_id,
                error=str(e),
            )
            # 发送失败/超时通常意味着连接已断开，需主动清理；但只清理失败时
            # 那条连接——挂起期间重连的新连接不能被误删（R5-M4）
            await self._evict_if_current(user_id, character_id, ws)
            return False

    async def broadcast(self, character_id: str, message: dict[str, Any]) -> int:
        """向某角色的所有在线用户广播消息（用于角色主动消息）

        Args:
            character_id: 角色 ID
            message: 待广播的字典（会被 JSON 序列化）

        Returns:
            成功推送的连接数
        """
        # 收集匹配连接（在锁内复制引用，锁外执行 IO）
        async with self._lock:
            targets = [(uid, cid, ws) for (uid, cid), ws in self._connections.items() if cid == character_id]

        if not targets:
            return 0

        success = 0
        # 记录失败连接的引用而非仅 key：清理时做同一性比较（R5-M4），
        # 广播期间重连的新连接会占住同一 key，不能被误删
        failed_targets: list[tuple[str, str, WebSocket]] = []
        for uid, cid, ws in targets:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=_WS_SEND_TIMEOUT_SECONDS)
                success += 1
            except Exception as e:
                logger.warning(
                    "ws_broadcast_send_failed",
                    user_id=uid,
                    character_id=cid,
                    error=str(e),
                )
                failed_targets.append((uid, cid, ws))

        # 清理失败连接（批量在锁内完成，避免逐条加锁）
        if failed_targets:
            async with self._lock:
                for uid, cid, ws in failed_targets:
                    # 仅当仍是失败时的同一连接才移除（避免误删新连接）
                    if self._connections.get((uid, cid)) is ws:
                        del self._connections[(uid, cid)]

        logger.info(
            "ws_broadcast_done",
            character_id=character_id,
            total=len(targets),
            success=success,
            failed=len(failed_targets),
        )
        return success

    async def get_connection_count(self) -> int:
        """返回当前活跃连接数（调试/监控用）"""
        async with self._lock:
            return len(self._connections)


# === WebSocket 路由 ===

# 独立 APIRouter，由 main.py 通过 app.include_router() 挂载
router = APIRouter()


def _parse_incoming(raw: str) -> str | None:
    """解析入站消息，返回用户文本内容

    支持两种格式：
    - 纯文本：直接返回
    - JSON：{"type": "message", "content": "..."}，取 content 字段

    Args:
        raw: WebSocket receive_text() 的原始字符串

    Returns:
        用户消息内容；无法解析时返回 None
    """
    text = raw.strip()
    if not text:
        return None

    # 尝试 JSON 解析（容错：失败则按纯文本处理）
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text  # 非法 JSON，按纯文本返回
        if isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                return content
            return None
        return None

    return text


def _safe_error(message: str) -> dict[str, Any]:
    """构造标准错误消息"""
    return {"type": "error", "message": message}


def _extract_bearer_subprotocol(websocket: WebSocket) -> str | None:
    """从 Sec-WebSocket-Protocol 头解析 bearer 子协议携带的 JWT（R4-L8）

    约定格式：`bearer, <token>`（浏览器 WebSocket 构造器的 subprotocols
    数组逐项以逗号拼接）。token 不再出现在 URL 中，避免被访问日志/
    中间代理记录。
    """
    header = websocket.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in header.split(",") if p.strip()]
    if len(parts) >= 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


async def _get_llm_globals() -> tuple[LLMClient | None, PromptTemplates | None]:
    """从 runtime 模块获取全局 llm / prompts（避免循环导入）

    Returns:
        (llm, prompts) 元组，启动期可能为 (None, None)
    """
    # 通过 runtime 依赖容器获取，避免反向依赖 main.py
    from src.runtime import get_llm, get_prompts

    return get_llm(), get_prompts()


@router.websocket("/ws/chat/{character_id}")
async def ws_chat_endpoint(
    websocket: WebSocket,
    character_id: str,
    user_id: str | None = None,
    platform: str = "web",
    token: str | None = None,
) -> None:
    """Web 客户端 WebSocket 聊天端点

    路径参数：
    - character_id: 角色 UUID

    查询参数：
    - user_id: 必填，用户标识
    - platform: 默认 "web"
    - token: JWT（兼容旧客户端的遗留方式；token 会进访问日志/中间代理日志，
      新客户端应改用 subprotocol，见 _extract_bearer_subprotocol）

    JWT 传递优先级与 /ws/dashboard 端点一致（R5-M12）：
    1. Sec-WebSocket-Protocol: bearer, <token>（首选：token 不进 URL）
    2. Authorization: Bearer 头
    3. 查询参数 token（遗留兼容）

    协议：
    - 入站：纯文本 或 {"type":"message","content":"..."}
    - 出站：
        - {"type":"connected","character_id":"..."}
        - {"type":"reply","content":"...","conversation_id":"...","message_id":"...","tokens":0,"cost":0.0}
        - {"type":"error","message":"..."}
    """
    # === 参数校验 ===
    if not user_id or not user_id.strip():
        await websocket.accept()
        await websocket.send_json(_safe_error("user_id query parameter is required"))
        await websocket.close(code=1008, reason="missing user_id")
        return

    user_id = user_id.strip()

    # === 鉴权（P0-8）：握手阶段校验 JWT，sub 必须与 user_id 一致 ===
    # 防止任何人用任意 user_id 冒充用户连接并读取其对话历史。
    # JWT 优先级与 dashboard 端点一致（R5-M12）；查询参数仅为旧客户端
    # 兼容保留——token 会进访问日志，新客户端必须走 subprotocol 或头
    subprotocol_token = _extract_bearer_subprotocol(websocket)
    if not token and subprotocol_token:
        token = subprotocol_token
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        await websocket.accept()
        await websocket.send_json(_safe_error("authentication required"))
        await websocket.close(code=1008, reason="missing token")
        return
    try:
        from src.auth import decode_token

        payload = decode_token(token)
    except Exception:
        await websocket.accept()
        await websocket.send_json(_safe_error("invalid token"))
        await websocket.close(code=1008, reason="invalid token")
        return
    if payload.get("sub") != user_id:
        await websocket.accept()
        await websocket.send_json(_safe_error("token subject mismatch"))
        await websocket.close(code=1008, reason="token subject mismatch")
        return

    try:
        cid = UUID(character_id)
    except ValueError:
        await websocket.accept()
        await websocket.send_json(_safe_error(f"Invalid character_id UUID: {character_id}"))
        await websocket.close(code=1008, reason="invalid character_id")
        return

    if platform not in ("web", "qq", "lark", "internal"):
        platform = "web"  # 非法平台降级为 web，不阻断连接

    # 获取 WebSocketManager 单例
    manager = WebSocketManager()

    # === 注册连接 ===
    # 客户端经 subprotocol 传 token 时 accept 必须回选该子协议（RFC 6455），
    # 否则浏览器端握手直接失败
    await manager.connect(websocket, user_id, character_id, subprotocol="bearer" if subprotocol_token else None)

    # 发送连接建立确认
    try:
        await websocket.send_json({"type": "connected", "character_id": character_id})
    except Exception as e:
        logger.warning(
            "ws_send_connected_failed",
            user_id=user_id,
            character_id=character_id,
            error=str(e),
        )

    # === 消息循环 ===
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                logger.info(
                    "ws_client_disconnected",
                    user_id=user_id,
                    character_id=character_id,
                )
                break

            # 解析入站消息
            content = _parse_incoming(raw)
            if content is None or not content.strip():
                await websocket.send_json(_safe_error("Message content cannot be empty"))
                continue

            # 获取 LLM 全局实例（启动期可能为 None）
            llm_client, prompts_obj = await _get_llm_globals()
            if llm_client is None or prompts_obj is None:
                await websocket.send_json(_safe_error("LLM client not initialized, please retry later"))
                continue

            # 调用 MessageService 处理用户消息
            try:
                async with db.session() as session:
                    svc = MessageService(
                        session=session,
                        llm=llm_client,
                        prompts=prompts_obj,
                    )
                    result = await svc.handle_user_message(
                        character_id=cid,
                        user_id=user_id,
                        platform=platform,
                        content=content,
                    )
            except Exception as e:
                logger.error(
                    "ws_message_handle_failed",
                    user_id=user_id,
                    character_id=character_id,
                    error=str(e),
                    exc_info=True,
                )
                await websocket.send_json(_safe_error(f"Message handling failed: {str(e)}"))
                continue

            # 构造并推送回复
            reply_payload = {
                "type": "reply",
                "content": result["content"],
                "conversation_id": str(result["conversation_id"]),
                "message_id": str(result["message_id"]) if result["message_id"] else "",
                "tokens": result["tokens"],
                "cost": result["cost"],
            }
            try:
                await websocket.send_json(reply_payload)
            except Exception as e:
                logger.warning(
                    "ws_send_reply_failed",
                    user_id=user_id,
                    character_id=character_id,
                    error=str(e),
                )
                # 发送失败通常意味着连接已断开，跳出循环
                break

    except WebSocketDisconnect:
        logger.info(
            "ws_client_disconnected_outer",
            user_id=user_id,
            character_id=character_id,
        )
    except Exception as e:
        # 兜底：未预期异常，记录但不让进程崩溃
        logger.error(
            "ws_unexpected_error",
            user_id=user_id,
            character_id=character_id,
            error=str(e),
            exc_info=True,
        )
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                # S-5：内部异常详情只进日志，不发给客户端
                await websocket.send_json(_safe_error("internal error, please retry"))
        except Exception:
            pass
    finally:
        # 确保从管理器中移除连接；同 key 可能已被重连的新连接占用
        # （connect 会关闭本会话这条旧 socket 触发本 finally），
        # 仅移除仍是本会话的连接（R5-M4 同源缺陷）
        await manager._evict_if_current(user_id, character_id, websocket)
        # 尽力关闭 socket
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000, reason="closing")
        except Exception:
            pass


# ============================================================
# Dashboard 实时推送（F-1：替代前端 5s/10s 轮询）
# ============================================================

_DASHBOARD_PUSH_INTERVAL = 5.0  # 世界状态推送周期（秒），与原轮询频率一致

# WS 发送超时（R4-M12）：与 OneBot 侧 10s 发送超时对齐；
# 半开连接上的 send_json 无超时会无限挂起调用方
_WS_SEND_TIMEOUT_SECONDS = 10.0


async def _dashboard_snapshot(redis: Any, notif_key: str) -> dict[str, Any]:
    """采集一帧仪表盘数据（世界状态 + 通知未读数）"""
    state = await redis.hgetall("world:state")
    tick_id = int(state["tick_id"]) if state.get("tick_id") else 0
    world_time_raw = str(state.get("world_time", ""))
    try:
        world_time = json.loads(world_time_raw)
        if not isinstance(world_time, str):
            world_time = world_time_raw
    except (json.JSONDecodeError, TypeError):
        world_time = world_time_raw

    notifications = await redis.lrange(notif_key, 0, -1)
    unread = 0
    for raw in notifications:
        try:
            if not json.loads(raw).get("read"):
                unread += 1
        except (json.JSONDecodeError, TypeError):
            continue

    return {
        "type": "dashboard",
        "world": {
            "tick_id": tick_id,
            "world_time": world_time,
            "weather": str(state.get("weather", "sunny")),
            "temperature": state.get("temperature"),
        },
        "notifications_unread": unread,
    }


@router.websocket("/ws/dashboard")
async def ws_dashboard_endpoint(
    websocket: WebSocket,
    token: str | None = None,
) -> None:
    """仪表盘实时推送端点

    JWT 传递方式（按优先级）：
    - Sec-WebSocket-Protocol: bearer, <token>（R4-L8 首选：token 不进 URL/访问日志）
    - Authorization: Bearer 头
    - 查询参数 token（兼容旧客户端）

    协议：
    - 出站：{"type":"dashboard","world":{...},"notifications_unread":N}
      每 _DASHBOARD_PUSH_INTERVAL 秒推一帧；无订阅者时零开销
    - 入站消息被忽略（纯推送通道）
    """
    subprotocol_token = _extract_bearer_subprotocol(websocket)
    if not token and subprotocol_token:
        token = subprotocol_token
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        await websocket.accept()
        await websocket.send_json(_safe_error("authentication required"))
        await websocket.close(code=1008, reason="missing token")
        return
    try:
        from src.auth import decode_token

        payload = decode_token(token)
    except Exception:
        await websocket.accept()
        await websocket.send_json(_safe_error("invalid token"))
        await websocket.close(code=1008, reason="invalid token")
        return

    user_id = str(payload.get("sub", ""))

    from src.runtime import get_redis, notification_key

    redis = get_redis()
    if redis is None:
        await websocket.accept()
        await websocket.send_json(_safe_error("redis not available"))
        await websocket.close(code=1011, reason="service unavailable")
        return

    notif_key = notification_key(user_id)
    # 客户端经 subprotocol 传 token 时，accept 需回选该子协议（RFC 6455），
    # 否则浏览器端握手直接失败
    await websocket.accept(subprotocol="bearer" if subprotocol_token else None)
    logger.info("ws_dashboard_connected", user_id=user_id)

    async def _push_frames() -> None:
        while True:
            snapshot = await _dashboard_snapshot(redis, notif_key)
            # 客户端断开可经异常感知退出循环；发送同样带超时（R4-M12），
            # 半开连接挂起会拖死整个推送协程
            await asyncio.wait_for(websocket.send_json(snapshot), timeout=_WS_SEND_TIMEOUT_SECONDS)
            await asyncio.sleep(_DASHBOARD_PUSH_INTERVAL)

    async def _receive_drain() -> None:
        # 不读业务消息，仅用于第一时间感知客户端断开（WebSocketDisconnect）
        while True:
            await websocket.receive_text()

    push_task = asyncio.create_task(_push_frames())
    recv_task = asyncio.create_task(_receive_drain())
    done, pending = await asyncio.wait({push_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass

    if recv_task in done and not recv_task.cancelled():
        exc = recv_task.exception()
        if isinstance(exc, WebSocketDisconnect):
            logger.info("ws_dashboard_disconnected", user_id=user_id)
        elif exc is not None:
            logger.warning("ws_dashboard_error", user_id=user_id, error=str(exc))
    elif push_task in done and not push_task.cancelled():
        exc = push_task.exception()
        if exc is not None:
            logger.warning("ws_dashboard_push_error", user_id=user_id, error=str(exc))

    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1000)
    except Exception:
        pass
