"""OneBot v11/v12 适配器 - QQ 机器人接入（反向 WebSocket）

职责：
1. 作为 WebSocket 服务端接收 OneBot 实现（NapCat / Lagrange 等）的反向连接
2. 解析 OneBot 事件（消息 / 元事件 / 心跳），转发消息至 MessageService
3. 通过 OneBot send_private_msg / send_group_msg action 向用户回推角色回复
4. 群聊接入：仅在 被@ 时回复，支持 群-角色 映射
5. 多段回复：长回复按段落拆分为多条消息依次发送（更像真人）
6. 主动分享推送：角色主动发起的分享通过 send_message 推送给有活跃会话的用户

设计要点：
- 反向 WebSocket：OneBot 实现主动连接本服务端（endpoint: /ws/onebot/v12）
- 用户映射：OneBot user_id -> (user_id="qq_{user_id}", platform="qq")
- 角色 ID 路由优先级：
  a. 群-角色映射（onebot_group_character_map）：不同群绑定不同角色
  b. 默认角色（ONEBOT_DEFAULT_CHARACTER_ID）
- 群聊 @ 检测：支持 OneBot 11 的 message 段数组 at 段、raw_message 的 [CQ:at] 码、to_me 字段
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
import re
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from structlog import get_logger

from src.db.session import db
from src.messaging import MessageService

logger = get_logger(__name__)


# 多段回复：每段最大长度（避免单条过长被截断）
MAX_SEGMENT_LENGTH = 500
# 多段回复：段落间发送间隔（秒），模拟真人打字
SEGMENT_SEND_INTERVAL = 0.6


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


def _get_group_character_map() -> dict[str, UUID]:
    """从配置读取群组-角色映射

    Returns:
        {group_id_str: character_uuid} 字典；解析失败返回空字典
    """
    from src.config import settings

    raw = settings.onebot_group_character_map or "{}"
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("onebot_group_character_map_invalid", value=raw)
        return {}

    result: dict[str, UUID] = {}
    for gid, cid in mapping.items():
        try:
            result[str(gid)] = UUID(str(cid))
        except (ValueError, TypeError):
            logger.warning(
                "onebot_group_mapping_invalid",
                group_id=gid,
                character_id=cid,
            )
    return result


def _get_configured_self_id() -> str | None:
    """从配置读取机器人自身 QQ 号（用于 @ 检测）"""
    from src.config import settings

    return settings.onebot_self_id


def _get_llm_globals() -> tuple[object | None, object | None, object | None]:
    """延迟获取全局 LLM 客户端与 Prompt 模板（避免循环导入）

    Returns:
        (llm, prompts, redis) 元组，启动期可能为 (None, None, None)
    """
    from src.main import llm, prompts, redis  # type: ignore

    return llm, prompts, redis


def _extract_text(event: dict) -> str:
    """从 OneBot v12 消息事件中提取纯文本

    优先使用 raw_message（OneBot v12 规范定义的纯文本表示）；
    若缺失则尝试从 message 段数组中拼接 text 段。

    Args:
        event: OneBot 事件字典

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


# 匹配 [CQ:at,qq=123456] 或 [CQ:at,qq=123456,name=xxx] 格式
_CQ_AT_PATTERN = re.compile(r"\[CQ:at,qq=(\d+)[^\]]*\]")


def _is_mentioned_self(event: dict, self_id: str | None) -> bool:
    """检测群聊消息是否 @ 了机器人

    检测顺序（任一命中即视为被 @）：
    1. event.to_me == true（OneBot 实现已判定）
    2. message 段数组含 at 段且 qq == self_id
    3. raw_message 含 [CQ:at,qq=<self_id>] 码

    Args:
        event: OneBot 事件字典
        self_id: 机器人自身 QQ 号（None 时仅靠 to_me 判断）

    Returns:
        是否被 @
    """
    # 1. OneBot 实现已判定
    if event.get("to_me") is True:
        return True

    if self_id is None:
        # 无 self_id 时只能靠 to_me，降级处理
        return False

    self_id_str = str(self_id)

    # 2. message 段数组检测
    message = event.get("message")
    if isinstance(message, list):
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "at":
                data = seg.get("data") or {}
                qq = str(data.get("qq", ""))
                if qq == self_id_str:
                    return True

    # 3. raw_message CQ 码检测
    raw_message = event.get("raw_message")
    if isinstance(raw_message, str):
        for match in _CQ_AT_PATTERN.finditer(raw_message):
            if match.group(1) == self_id_str:
                return True

    return False


def _strip_at_prefix(event: dict, self_id: str | None, text: str) -> str:
    """移除消息中的 @机器人 前缀，保留实际内容

    Args:
        event: OneBot 事件字典
        self_id: 机器人自身 QQ 号
        text: 原始提取文本

    Returns:
        清理后的文本
    """
    if not self_id:
        return text

    self_id_str = str(self_id)

    # 移除 [CQ:at,qq=<self_id>...] 码
    def _replace_at(m: re.Match) -> str:
        return "" if m.group(1) == self_id_str else m.group(0)

    cleaned = _CQ_AT_PATTERN.sub(_replace_at, text)

    # 如果 message 段数组以 at 段开头，移除对应的纯文本空格
    message = event.get("message")
    if isinstance(message, list):
        # 重建纯文本（跳过指向机器人的 at 段）
        parts: list[str] = []
        for seg in message:
            if isinstance(seg, dict):
                if seg.get("type") == "at":
                    data = seg.get("data") or {}
                    if str(data.get("qq", "")) == self_id_str:
                        continue
                    # 非 @ 机器人的 at 段保留为文本
                    parts.append(f"@{data.get('qq', '')}")
                elif seg.get("type") == "text":
                    data = seg.get("data") or {}
                    t = data.get("text", "")
                    if isinstance(t, str):
                        parts.append(t)
        cleaned = "".join(parts).strip()

    return cleaned.strip()


def _split_message(text: str) -> list[str]:
    """将长回复拆分为多段消息

    拆分策略：
    1. 优先按双换行（段落）拆分
    2. 单段超过 MAX_SEGMENT_LENGTH 时按单换行继续拆分
    3. 仍超长则硬切分

    Args:
        text: 待发送的完整回复文本

    Returns:
        拆分后的消息段列表（已 strip，过滤空段）
    """
    if not text:
        return []

    text = text.strip()
    if not text:
        return []

    # 单段足够短，直接返回
    if len(text) <= MAX_SEGMENT_LENGTH and "\n\n" not in text:
        return [text]

    segments: list[str] = []

    # 1. 按双换行（段落）拆分
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 2. 单段仍超长，按单换行拆分
        if len(para) > MAX_SEGMENT_LENGTH:
            lines = para.split("\n")
            buf = ""
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # 3. 仍超长则硬切分
                while len(line) > MAX_SEGMENT_LENGTH:
                    if buf:
                        segments.append(buf)
                        buf = ""
                    segments.append(line[:MAX_SEGMENT_LENGTH])
                    line = line[MAX_SEGMENT_LENGTH:]
                # 累积到 buf
                if buf and len(buf) + len(line) + 1 <= MAX_SEGMENT_LENGTH:
                    buf += "\n" + line
                else:
                    if buf:
                        segments.append(buf)
                    buf = line
            if buf:
                segments.append(buf)
        else:
            segments.append(para)

    return [s for s in segments if s.strip()]


class OneBotAdapter:
    """OneBot v11/v12 反向 WebSocket 适配器

    OneBot 实现（NapCat / Lagrange 等）作为客户端主动连接本服务端，
    本适配器在 /ws/onebot/v12 端点接受连接并处理事件。

    功能：
    - 群聊接入：仅在 被@ 时回复（可配置），支持 群-角色 映射
    - 多段回复：长回复按段落拆分为多条消息依次发送
    - 主动分享推送：通过 push_share 推送角色主动消息给指定用户/群
    """

    def __init__(self) -> None:
        self.router = APIRouter()
        self.router.websocket("/ws/onebot/v12")(self._ws_endpoint)

        # 活跃连接集合（用于广播与生命周期管理）
        # 注意：OneBot 实现通常只有 1 个连接，这里保留 set 以支持多实例
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._running = False

    async def start(self) -> None:
        """启动适配器（标记运行状态，路由由 FastAPI 自动接管）"""
        self._running = True
        default_cid = _get_default_character_id()
        group_map = _get_group_character_map()
        logger.info(
            "onebot_adapter_started",
            endpoint="/ws/onebot/v12",
            default_character_id=str(default_cid) if default_cid else None,
            group_mappings=len(group_map),
            at_only=_get_at_only(),
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
        """OneBot v11/v12 反向 WebSocket 入口端点

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
                        event_type=event.get("type") or event.get("post_type"),
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
        1. 提取 user_id / group_id / message_type / raw_message / self_id
        2. 群聊消息：
           a. 若开启 at_only，检测是否 @ 了机器人，未 @ 则跳过
           b. 从 群-角色映射 或默认角色解析 character_id
           c. 移除消息中的 @机器人 前缀
        3. 私聊消息：使用默认角色
        4. 映射到内部用户标识 (qq_{user_id}, platform=qq)
        5. 调用 MessageService.handle_user_message 生成角色回复
        6. 通过 send_message 回推回复（支持多段）
        """
        # 兼容 OneBot v12 (detail_type) 和 OneBot 11 (message_type)
        detail_type = event.get("detail_type") or event.get("message_type")
        user_id = event.get("user_id")
        group_id = event.get("group_id")
        raw_message = _extract_text(event)
        # self_id 优先从事件读取，其次从配置读取
        self_id = str(event.get("self_id") or "") or _get_configured_self_id()

        is_group = detail_type == "group"

        logger.info(
            "onebot_message_received",
            detail_type=detail_type,
            user_id=user_id,
            group_id=group_id,
            raw_message=raw_message[:100],
            is_group=is_group,
        )

        if not raw_message:
            logger.info("onebot_empty_message_skipped", user_id=user_id, group_id=group_id)
            return

        # 群聊接入：智能回复决策
        if is_group:
            at_only = _get_at_only()
            mentioned = _is_mentioned_self(event, self_id)

            if mentioned:
                # 被 @ 时总是回复，移除 @ 前缀保留实际内容
                raw_message = _strip_at_prefix(event, self_id, raw_message)
                if not raw_message:
                    logger.info(
                        "onebot_group_at_only_no_content",
                        group_id=group_id,
                        user_id=user_id,
                    )
                    return
            elif at_only:
                # at_only 模式下，未 @ 则跳过
                logger.debug(
                    "onebot_group_not_at_skipped",
                    group_id=group_id,
                    user_id=user_id,
                )
                return
            else:
                # 智能回复模式：读取所有群消息，决策是否回复
                should, reason = await self._should_reply_in_group(
                    raw_message, user_id, onebot_ws
                )
                if not should:
                    logger.info(
                        "onebot_group_smart_skip",
                        group_id=group_id,
                        user_id=user_id,
                        reason=reason,
                        message_preview=raw_message[:50],
                    )
                    return
                logger.info(
                    "onebot_group_smart_reply",
                    group_id=group_id,
                    user_id=user_id,
                    reason=reason,
                )

        # 解析角色 ID
        character_id = _resolve_character_id(is_group, group_id)
        if character_id is None:
            logger.warning(
                "onebot_character_not_configured",
                hint="Set ONEBOT_DEFAULT_CHARACTER_ID or onebot_group_character_map",
                is_group=is_group,
                group_id=group_id,
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
        llm_client, prompts_obj, redis_client = _get_llm_globals()
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

        # 映射到内部用户标识
        internal_user_id = f"qq_{user_id}" if user_id is not None else "qq_unknown"

        # 调用 MessageService 处理用户消息
        try:
            async with db.session() as session:
                svc = MessageService(
                    session=session,
                    llm=llm_client,  # type: ignore[arg-type]
                    prompts=prompts_obj,  # type: ignore[arg-type]
                    redis=redis_client,
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

        # 回推角色回复（支持多段）
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
        """处理 OneBot 元事件（心跳 / 生命周期）

        兼容 OneBot v12 (detail_type) 和 OneBot 11 (meta_event_type)。
        仅记录日志，不做业务处理。
        """
        detail_type = event.get("detail_type") or event.get("meta_event_type")
        if detail_type == "heartbeat":
            logger.debug(
                "onebot_heartbeat",
                status=event.get("status"),
                interval=event.get("interval"),
            )
        elif detail_type in ("lifecycle", "enable", "disable"):
            logger.info(
                "onebot_lifecycle",
                sub_type=event.get("sub_type") or detail_type,
            )
        else:
            logger.debug("onebot_meta_event", detail_type=detail_type)

    async def _should_reply_in_group(
        self, message: str, sender_user_id: str | int | None, onebot_ws: WebSocket
    ) -> tuple[bool, str]:
        """群聊智能回复决策 - 调用 MessageService.should_reply_in_group

        流程：
        1. 获取 LLM 全局实例
        2. 解析角色 ID 和角色名
        3. 调用 MessageService.should_reply_in_group 判断是否回复

        Args:
            message: 群聊消息纯文本
            sender_user_id: 发送者 QQ 号
            onebot_ws: OneBot WebSocket 连接

        Returns:
            (should_reply, reason)
        """
        llm_client, prompts_obj, redis_client = _get_llm_globals()
        if llm_client is None or prompts_obj is None:
            return False, "llm_not_ready"

        # 解析角色 ID（群聊场景）
        character_id = _resolve_character_id(is_group=True, group_id=None)
        if character_id is None:
            return False, "character_not_configured"

        # 获取角色名
        character_name = ""
        try:
            async with db.session() as session:
                from src.db.repositories import CharacterRepository

                char_repo = CharacterRepository(session)
                character = await char_repo.get_by_id(character_id)
                if character is not None:
                    character_name = character.name
        except Exception as e:
            logger.warning(
                "group_reply_load_character_failed",
                character_id=str(character_id),
                error=str(e),
            )
            return False, "character_load_error"

        if not character_name:
            return False, "character_name_empty"

        # 调用 MessageService.should_reply_in_group
        try:
            async with db.session() as session:
                svc = MessageService(
                    session=session,
                    llm=llm_client,  # type: ignore[arg-type]
                    prompts=prompts_obj,  # type: ignore[arg-type]
                    redis=redis_client,
                )
                internal_user_id = f"qq_{sender_user_id}" if sender_user_id is not None else "qq_unknown"
                return await svc.should_reply_in_group(
                    character_id=character_id,
                    character_name=character_name,
                    message=message,
                    sender_user_id=internal_user_id,
                )
        except Exception as e:
            logger.error(
                "group_reply_decision_failed",
                error=str(e),
                exc_info=True,
            )
            return False, f"decision_error:{type(e).__name__}"

    async def send_message(
        self,
        onebot_ws: WebSocket,
        event_type: str,
        user_id: str | int | None,
        group_id: str | int | None,
        message: str,
    ) -> None:
        """通过 OneBot action 回推消息（兼容 OneBot 11 和 v12）

        优先使用 OneBot 11 的 send_private_msg / send_group_msg，
        因为主流实现（NapCat / Lagrange）对 OneBot 11 API 支持更完善。

        支持多段回复：长消息按段落拆分，依次发送多条消息。

        Args:
            onebot_ws: OneBot 实现的 WebSocket 连接
            event_type: 目标会话类型（"private" 或 "group"）
            user_id: OneBot 用户 ID（私聊必填）
            group_id: OneBot 群 ID（群聊必填）
            message: 待发送的纯文本消息（可能含多段）
        """
        # 拆分为多段
        segments = _split_message(message)
        if not segments:
            return

        for idx, seg in enumerate(segments):
            await self._send_single(
                onebot_ws=onebot_ws,
                event_type=event_type,
                user_id=user_id,
                group_id=group_id,
                message=seg,
                segment_index=idx,
                segment_total=len(segments),
            )
            # 多段之间添加间隔，避免刷屏
            if idx < len(segments) - 1:
                await asyncio.sleep(SEGMENT_SEND_INTERVAL)

    async def _send_single(
        self,
        onebot_ws: WebSocket,
        event_type: str,
        user_id: str | int | None,
        group_id: str | int | None,
        message: str,
        segment_index: int = 0,
        segment_total: int = 1,
    ) -> None:
        """发送单条消息（内部使用，send_message 调用）

        Args:
            segment_index: 当前段索引（0-based）
            segment_total: 总段数
        """
        is_group = event_type == "group"

        if is_group:
            if group_id is None:
                logger.warning("onebot_send_missing_group_id", user_id=user_id)
                return
            # OneBot 11: send_group_msg
            action_name = "send_group_msg"
            params: dict = {
                "group_id": group_id,
                "message": message,
            }
        else:
            if user_id is None:
                logger.warning("onebot_send_missing_user_id", group_id=group_id)
                return
            # OneBot 11: send_private_msg
            action_name = "send_private_msg"
            params = {
                "user_id": user_id,
                "message": message,
            }

        action = {
            "action": action_name,
            "params": params,
        }

        # 发送前检查 WebSocket 连接是否仍然存活
        if onebot_ws.client_state != WebSocketState.CONNECTED:
            logger.warning(
                "onebot_send_ws_disconnected",
                event_type=event_type,
                user_id=user_id,
                group_id=group_id,
            )
            return

        try:
            await onebot_ws.send_text(json.dumps(action, ensure_ascii=False))
            logger.info(
                "onebot_message_sent",
                event_type=event_type,
                user_id=user_id,
                group_id=group_id,
                message_length=len(message),
                segment_index=segment_index,
                segment_total=segment_total,
            )
        except RuntimeError as e:
            # WebSocket 已关闭（处理 LLM 回复期间连接断开）
            logger.warning(
                "onebot_send_ws_closed",
                event_type=event_type,
                user_id=user_id,
                error=str(e),
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

    async def push_share(
        self,
        user_id: str | int | None = None,
        group_id: str | int | None = None,
        message: str = "",
    ) -> bool:
        """主动推送分享消息给指定用户/群（无需用户先发消息）

        用于 ProactiveSharingService 在角色产生分享意图时主动推送。
        会自动使用第一个活跃的 OneBot 连接发送。

        Args:
            user_id: 私聊用户 ID（与 group_id 二选一）
            group_id: 群 ID（与 user_id 二选一）
            message: 分享文案

        Returns:
            是否成功推送
        """
        if not message:
            return False

        # 优先群聊，其次私聊
        if group_id is not None:
            event_type = "group"
        elif user_id is not None:
            event_type = "private"
        else:
            logger.warning("onebot_push_share_no_target")
            return False

        # 获取第一个活跃连接
        async with self._lock:
            conns = list(self._connections)
        if not conns:
            logger.warning(
                "onebot_push_share_no_connection",
                user_id=user_id,
                group_id=group_id,
            )
            return False

        ws = conns[0]
        try:
            await self.send_message(
                onebot_ws=ws,
                event_type=event_type,
                user_id=user_id,
                group_id=group_id,
                message=message,
            )
            logger.info(
                "onebot_share_pushed",
                event_type=event_type,
                user_id=user_id,
                group_id=group_id,
                message_length=len(message),
            )
            return True
        except Exception as e:
            logger.error(
                "onebot_push_share_failed",
                user_id=user_id,
                group_id=group_id,
                error=str(e),
                exc_info=True,
            )
            return False


def _get_at_only() -> bool:
    """从配置读取群聊是否仅在被 @ 时回复"""
    from src.config import settings

    return settings.onebot_group_at_only


def _resolve_character_id(is_group: bool, group_id: str | int | None) -> UUID | None:
    """解析消息对应的角色 ID

    优先级：
    1. 群聊时：群-角色映射（onebot_group_character_map）
    2. 默认角色（ONEBOT_DEFAULT_CHARACTER_ID）

    Args:
        is_group: 是否为群聊
        group_id: 群 ID

    Returns:
        角色 UUID；未配置返回 None
    """
    if is_group and group_id is not None:
        group_map = _get_group_character_map()
        cid = group_map.get(str(group_id))
        if cid is not None:
            return cid

    return _get_default_character_id()
