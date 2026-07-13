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
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from structlog import get_logger

from src.db.session import db
from src.llm import LLMClient, PromptTemplates
from src.messaging import MessageService

logger = get_logger(__name__)


class WebSocketManager:
    """WebSocket 连接管理器（单例）

    连接索引：{(user_id, character_id): WebSocket}
    - 同一 (user_id, character_id) 仅保留最新连接，旧连接被覆盖前会尝试关闭
    - broadcast(character_id) 会遍历所有匹配该角色的连接
    """

    _instance: "WebSocketManager | None" = None

    def __new__(cls) -> "WebSocketManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False  # type: ignore[attr-defined]
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
    ) -> None:
        """注册一条 WebSocket 连接

        - 先 accept，再写入连接表
        - 若同 (user_id, character_id) 已存在旧连接，尝试关闭旧连接（避免资源泄漏）

        Args:
            websocket: FastAPI WebSocket 对象
            user_id: 用户标识
            character_id: 角色 ID（字符串形式 UUID）
        """
        await websocket.accept()

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

    async def send_to_user(
        self,
        user_id: str,
        character_id: str,
        message: dict,
    ) -> bool:
        """向指定 (user_id, character_id) 推送 JSON 消息

        Args:
            user_id: 用户标识
            character_id: 角色 ID
            message: 待发送的字典（会被 JSON 序列化）

        Returns:
            True 表示发送成功，False 表示连接不存在或发送失败
        """
        async with self._lock:
            ws = self._connections.get((user_id, character_id))

        if ws is None:
            return False

        try:
            await ws.send_json(message)
            return True
        except Exception as e:
            logger.warning(
                "ws_send_to_user_failed",
                user_id=user_id,
                character_id=character_id,
                error=str(e),
            )
            # 发送失败通常意味着连接已断开，主动清理
            await self.disconnect(user_id, character_id)
            return False

    async def broadcast(self, character_id: str, message: dict) -> int:
        """向某角色的所有在线用户广播消息（用于角色主动消息）

        Args:
            character_id: 角色 ID
            message: 待广播的字典

        Returns:
            成功推送的连接数
        """
        # 收集匹配连接（在锁内复制引用，锁外执行 IO）
        async with self._lock:
            targets = [
                (uid, cid, ws)
                for (uid, cid), ws in self._connections.items()
                if cid == character_id
            ]

        if not targets:
            return 0

        success = 0
        failed_keys: list[tuple[str, str]] = []
        for uid, cid, ws in targets:
            try:
                await ws.send_json(message)
                success += 1
            except Exception as e:
                logger.warning(
                    "ws_broadcast_send_failed",
                    user_id=uid,
                    character_id=cid,
                    error=str(e),
                )
                failed_keys.append((uid, cid))

        # 清理失败连接
        if failed_keys:
            async with self._lock:
                for key in failed_keys:
                    # 仅当仍是失败时的同一连接才移除（避免误删新连接）
                    if self._connections.get(key) is not None:
                        self._connections.pop(key, None)

        logger.info(
            "ws_broadcast_done",
            character_id=character_id,
            total=len(targets),
            success=success,
            failed=len(failed_keys),
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


def _safe_error(message: str) -> dict:
    """构造标准错误消息"""
    return {"type": "error", "message": message}


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
):
    """Web 客户端 WebSocket 聊天端点

    路径参数：
    - character_id: 角色 UUID

    查询参数：
    - user_id: 必填，用户标识
    - platform: 默认 "web"

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
    await manager.connect(websocket, user_id, character_id)

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
                await websocket.send_json(
                    _safe_error("LLM client not initialized, please retry later")
                )
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
                await websocket.send_json(
                    _safe_error(f"Message handling failed: {str(e)}")
                )
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
                await websocket.send_json(_safe_error(f"Unexpected error: {str(e)}"))
        except Exception:
            pass
    finally:
        # 确保从管理器中移除连接
        await manager.disconnect(user_id, character_id)
        # 尽力关闭 socket
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000, reason="closing")
        except Exception:
            pass
