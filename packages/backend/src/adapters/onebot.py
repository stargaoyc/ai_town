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
- LLM 客户端通过 `from src.runtime import get_llm, get_prompts, get_redis` 延迟获取，避免循环导入
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
import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from prometheus_client import Counter
from starlette.websockets import WebSocketState
from structlog import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.llm import LLMClient, PromptTemplates

from src.db.session import db
from src.messaging import MessageService

logger = get_logger(__name__)


# 多段回复：每段最大长度（避免单条过长被截断）
MAX_SEGMENT_LENGTH = 500
# 多段回复：段落间发送间隔（秒），模拟真人打字
SEGMENT_SEND_INTERVAL = 0.6
# 回复去重键 TTL（秒）：与原实现一致，防止 OneBot 实现重发同一事件导致重复回复
_REPLY_DEDUP_TTL_SECONDS = 600
# 出站帧发送超时（秒，M17）：半开连接的 send_text 会永久阻塞，必须限时后
# 走跨连接 failover / 驱逐。config.py 本轮禁改，先以模块常量落地。
_SEND_TIMEOUT_SECONDS = 10.0

# 群共享上下文环容量与过期（R4-M14）
_GROUP_CONTEXT_MAX_MESSAGES = 20
_GROUP_CONTEXT_TTL_SECONDS = 24 * 3600
# 入站限流固定窗口宽度（秒，R5-M7）：窗口与 onebot_rate_limit_per_minute 配套
_RATE_LIMIT_WINDOW_SECONDS = 60
# 心跳新鲜度阈值（秒，M17）：约 2 倍常见 OneBot 心跳间隔（30s）。超过该时长
# 未收到心跳的连接在选择发送目标时排到新鲜连接之后。
_HEARTBEAT_FRESH_SECONDS = 60.0

# QQ 层 action 响应可观测（M12）：此前 send-action 的 retcode 响应被当作未知
# 事件吞掉，QQ 侧发送失败（风控 / 禁言 / 目标不存在）完全不可见。
ONEBOT_ACTION_RESPONSE_TOTAL = Counter(
    "ai_town_onebot_action_response_total",
    "OneBot send-action 响应次数",
    ["outcome"],  # outcome: success/failed
)


def _reply_dedup_key(message_id: str) -> str:
    """回复去重键（键格式与原实现保持一致）"""
    return f"onebot:msg:{message_id}"


def _parse_action_response(data: dict[str, Any]) -> dict[str, Any] | None:
    """识别并归一化 OneBot action 响应帧（round-3 M12）

    v11/v12 的 action 响应形如 {"status": ..., "retcode": ..., "data": ..., "echo": ...}：
    - v11：retcode 为 int，0 成功、1 已转异步、1200+ 各类失败
    - v12：status 为字符串（"ok"/"failed"），retcode 恒为 int

    成功判定：retcode 属于 {0, 1} 且 status 非 "failed"（1=async 视为已受理，
    结果经回调异步送达，此处只关心同步可判定的失败）。

    Returns:
        归一化结果 {"ok": bool, "status": str, "retcode": Any, "echo": Any}；
        非响应帧（正常事件）返回 None
    """
    if "post_type" in data or "type" in data:
        return None
    if "retcode" not in data or "status" not in data:
        return None

    retcode = data["retcode"]
    status = str(data["status"]).lower()
    ok = retcode in (0, 1) and status != "failed"
    return {
        "ok": ok,
        "status": status,
        "retcode": retcode,
        "echo": data.get("echo"),
    }


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


# 群-角色映射解析缓存（TTL 5s）：每条群消息都重新 json.loads + UUID 解析
# 属重复开销；短 TTL 兼容运行时热更新配置（审查 P3）
_GROUP_MAP_CACHE_TTL = 5.0
_group_map_cache: tuple[float, dict[str, UUID]] | None = None


def _get_group_character_map() -> dict[str, UUID]:
    """从配置读取群组-角色映射（带 5 秒 TTL 缓存）

    Returns:
        {group_id_str: character_uuid} 字典；解析失败返回空字典
    """
    global _group_map_cache

    import time as _time

    now = _time.monotonic()
    if _group_map_cache is not None and now - _group_map_cache[0] < _GROUP_MAP_CACHE_TTL:
        return _group_map_cache[1]

    from src.config import settings

    raw = settings.onebot_group_character_map or "{}"
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("onebot_group_character_map_invalid", value=raw)
        result: dict[str, UUID] = {}
    else:
        result = {}
        for gid, cid in mapping.items():
            try:
                result[str(gid)] = UUID(str(cid))
            except (ValueError, TypeError):
                logger.warning(
                    "onebot_group_mapping_invalid",
                    group_id=gid,
                    character_id=cid,
                )

    _group_map_cache = (now, result)
    return result


def _get_configured_self_id() -> str | None:
    """从配置读取机器人自身 QQ 号（用于 @ 检测）"""
    from src.config import settings

    return settings.onebot_self_id


# R5-L10：self_id 全缺失只告警一次——每条消息事件都会走到该分支，逐条告警等于刷屏
_SELF_ID_WARNING_EMITTED = False


def _warn_self_id_missing() -> None:
    """事件与配置均无 self_id 时给出一次性显式信号

    self_id 缺失时回显抑制（user_id == self_id）恒为假，机器人可能对自己的
    回显再次回复形成死循环；此前该失效完全静默。
    """
    global _SELF_ID_WARNING_EMITTED
    if _SELF_ID_WARNING_EMITTED:
        return
    _SELF_ID_WARNING_EMITTED = True
    logger.warning(
        "onebot_self_id_unavailable",
        hint="事件与 ONEBOT_SELF_ID 均未提供 self_id，自身回显抑制已失效，建议设置 ONEBOT_SELF_ID",
    )


def _get_llm_globals() -> tuple[LLMClient | None, PromptTemplates | None, Redis | None]:
    """延迟获取全局 LLM 客户端与 Prompt 模板（避免循环导入）

    Returns:
        (llm, prompts, redis) 元组，启动期可能为 (None, None, None)
    """
    from src.runtime import get_llm, get_prompts, get_redis

    return get_llm(), get_prompts(), get_redis()


def _extract_text(event: dict[str, Any]) -> str:
    """从 OneBot v11/v12 消息事件中提取纯文本

    优先使用 raw_message；缺失则从 message 段数组拼接 text 段。
    非文本段（图片/语音/视频/文件）以占位符并入正文（P2-13）：
    此前被静默丢弃，角色对用户发的图完全无感知。
    """
    raw_message = event.get("raw_message")
    if isinstance(raw_message, str) and raw_message.strip():
        return raw_message.strip()

    message = event.get("message")
    if isinstance(message, list):
        parts: list[str] = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type")
            data = seg.get("data") or {}
            if seg_type == "text":
                text = data.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
            elif seg_type in ("image", "voice", "record", "video", "file"):
                label = {"voice": "语音", "record": "语音"}.get(seg_type, seg_type)
                parts.append(f"[{label}]")
        return "".join(parts).strip()

    return ""


# 匹配 [CQ:at,qq=123456] 或 [CQ:at,qq=123456,name=xxx] 格式
_CQ_AT_PATTERN = re.compile(r"\[CQ:at,qq=(\d+)[^\]]*\]")


def _is_mentioned_self(event: dict[str, Any], self_id: str | None) -> bool:
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


def _strip_at_prefix(event: dict[str, Any], self_id: str | None, text: str) -> str:
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
    def _replace_at(m: re.Match[str]) -> str:
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


# 出站媒体直链（生成→QQ 链路）：仅 http(s) 且以常见媒体扩展名结尾的地址
# P3-1：视频侧补充流式与常见容器格式（m3u8/mp4/mov/webm/avi/mkv/flv）
_IMAGE_URL_RE = re.compile(
    r"https?://[^\s\]]+?\.(?:png|jpe?g|gif|webp)(?:\?[^\s\]]*)?",
    re.IGNORECASE,
)
_VIDEO_URL_RE = re.compile(
    r"https?://[^\s\]]+?\.(?:mp4|mov|webm|avi|mkv|flv|m3u8)(?:\?[^\s\]]*)?",
    re.IGNORECASE,
)
# 其余全部 CQ 码一律剥离——防止提示注入伪造 at/reply/JSON 等动作
_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]*\]")


def sanitize_outbound_qq_text(text: str) -> str:
    """出站消息净化：剥离其余 CQ 码，正文中的媒体直链转为对应 CQ

    顺序关键：先剥离全部 CQ 码（连同其内部参数一起移除，
    防止提示注入伪造 at/reply/JSON 等动作或夹带恶意 URL），再从
    剩余正文中提取图片/视频直链转 [CQ:image]/[CQ:video]。

    全部剥空时返回空串（send_message 对空串直接跳过发送）。
    """
    cleaned = _CQ_CODE_RE.sub("", text)
    images = _IMAGE_URL_RE.findall(cleaned)
    videos = _VIDEO_URL_RE.findall(cleaned)
    cleaned = cleaned.strip()
    cq_suffix = "\n".join([f"[CQ:image,file={u}]" for u in images] + [f"[CQ:video,file={u}]" for u in videos])
    if cq_suffix:
        cleaned = (cleaned + "\n" if cleaned else "") + cq_suffix
    return cleaned


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
        # R5-H5：构造即暴露 /ws/onebot/v12 路由，令牌检查必须在路由可用前完成；
        # 放在 start() 会被 main.py 的降级 try/except 吞掉，生产 fail-fast 失效
        from src.security.startup_checks import check_onebot_access_token

        check_onebot_access_token()

        self.router = APIRouter()
        self.router.websocket("/ws/onebot/v12")(self._ws_endpoint)

        # 活跃连接集合，用于广播和主动回复
        # 注意：OneBot 实现通常只有 1 个连接，这里保留 set 以支持多实例
        self._connections: set[WebSocket] = set()
        # 连接附属信息（M14/M17）：键与 _connections 同生命周期，_unregister/_evict
        # 同步清理。心跳/账号由元事件携带，连接建立初期合法地为空。
        self._last_heartbeat: dict[WebSocket, float] = {}
        self._conn_self_id: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()
        self._running = False
        # 断连兜底：启动时重放队列中未确认的消息事件（后台任务）
        self._recovery_task: asyncio.Task[None] | None = None

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
        # 断连兜底（审查清单 #5）：重放崩溃/重启期间未确认的入站消息。
        # 重放幂等性由消息侧 SETNX 去重保证，不会重复回复。
        self._recovery_task = asyncio.create_task(self._recovery_loop())

    async def _recovery_loop(self) -> None:
        """周期性重放队列中未确认的消息事件"""
        from src.messaging.event_queue import EventQueue
        from src.runtime import get_redis

        while self._running:
            try:
                redis_client = get_redis()
                if redis_client is None:
                    await asyncio.sleep(30)
                    continue
                queue = EventQueue(redis_client)

                async def _replay(ev: dict[str, Any]) -> None:
                    # 每条事件按其 self_id 选同账号连接（M14：多账号部署下若绑
                    # 任意连接，重放的回复会从错误 QQ 号发出）；无匹配账号时退回
                    # 任意活跃连接，单账号部署行为不变。
                    # 无可用连接必须抛错：静默返回会让 recover_drain 把条目
                    # XDEL 掉而回复未发出。
                    ws = await self._ws_for_self_id(ev.get("self_id")) or await self._any_ws()
                    if ws is None:
                        raise RuntimeError("onebot_replay_no_connection")
                    await self.handle_event(ev, ws)

                replayed = await queue.recover_drain(_replay, max_entries=100)
                if replayed:
                    logger.info("onebot_events_replayed", count=replayed)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("onebot_recovery_failed", error=str(e))
            await asyncio.sleep(15)

    async def _any_ws(self) -> WebSocket | None:
        """取一个活跃 OneBot 连接；近期有心跳的连接优先于陈旧连接（M17）"""
        connected = await self._connected_snapshot()
        if not connected:
            return None
        fresh = self._fresh_first(connected)
        return fresh[0] if fresh else connected[0]

    async def _ws_for_self_id(self, self_id: Any) -> WebSocket | None:
        """选择 self_id 匹配的活跃连接（多账号部署下重放必须走同账号出口）

        Returns:
            匹配的连接（心跳新鲜者优先）；无匹配账号连接时返回 None，
            由调用方决定是否退回 _any_ws()
        """
        target = str(self_id) if self_id is not None else ""
        if not target:
            return None
        connected = await self._connected_snapshot()
        matches = [ws for ws in connected if self._conn_self_id.get(ws) == target]
        if not matches:
            return None
        fresh = self._fresh_first(matches)
        return fresh[0] if fresh else matches[0]

    async def _connected_snapshot(self) -> list[WebSocket]:
        """锁内快照当前 CONNECTED 的连接列表"""
        async with self._lock:
            return [ws for ws in self._connections if ws.client_state == WebSocketState.CONNECTED]

    def _fresh_first(self, conns: list[WebSocket]) -> list[WebSocket]:
        """按心跳新鲜度过滤：从未收到心跳的新连接视为新鲜（delta=0）"""
        now = time.monotonic()
        return [ws for ws in conns if now - self._last_heartbeat.get(ws, now) < _HEARTBEAT_FRESH_SECONDS]

    async def stop(self) -> None:
        """停止适配器，关闭所有 OneBot 连接"""
        self._running = False
        if self._recovery_task:
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass

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
            # 心跳/账号记录可能尚未建立（连接后未收到任何元事件），允许缺键
            self._last_heartbeat.pop(websocket, None)
            self._conn_self_id.pop(websocket, None)

    async def _ws_endpoint(self, websocket: WebSocket) -> None:
        """OneBot v11/v12 反向 WebSocket 入口端点

        协议：OneBot 实现连接后逐条推送事件 JSON（文本帧），
        本端点解析事件并分发到对应处理器。
        """
        # P0-8：access-token 校验（OneBot 标准：Authorization: Bearer 或 access_token 参数）
        # 配置 onebot_access_token 后强制校验，防止任何人冒充 OneBot 实现注入伪造事件
        from src.config import settings

        expected = settings.onebot_access_token
        if expected:
            presented = websocket.query_params.get("access_token")
            if not presented:
                auth_header = websocket.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    presented = auth_header[7:]
            if not presented or not secrets.compare_digest(presented, expected):
                logger.warning("onebot_auth_failed")
                await websocket.close(code=1008, reason="invalid access token")
                return
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

                # M12：send-action 的 retcode 响应不是事件帧。此前落入
                # unknown_event 分支被吞掉，QQ 层发送失败完全不可观测，
                # push_share 的 failover 也只能对 ws 级错误生效。
                action_resp = _parse_action_response(event)
                if action_resp is not None:
                    ONEBOT_ACTION_RESPONSE_TOTAL.labels(outcome="success" if action_resp["ok"] else "failed").inc()
                    log = logger.debug if action_resp["ok"] else logger.warning
                    log(
                        "onebot_action_response_ok" if action_resp["ok"] else "onebot_action_response_failed",
                        status=action_resp["status"],
                        retcode=action_resp["retcode"],
                        echo=action_resp["echo"],
                    )
                    continue

                # 断连兜底：消息事件先持久化到 Streams（处理成功后 XACK+XDEL 从
                # 流中移除，见 event_queue 模块 docstring 的 round-3 H3 说明；
                # 崩溃/重启后由 _recovery_loop 重放，幂等性由 SETNX 去重保证）
                queue = None
                entry_id = None
                if event.get("post_type") == "message":
                    from src.runtime import get_redis

                    redis_client = get_redis()
                    if redis_client is not None:
                        from src.messaging.event_queue import EventQueue

                        queue = EventQueue(redis_client)
                        try:
                            entry_id = await queue.enqueue(event)
                        except Exception as e:
                            logger.warning("onebot_event_enqueue_failed", error=str(e))
                            queue = None

                try:
                    await self.handle_event(event, websocket)
                except Exception as e:
                    # 单条事件处理失败不影响后续事件；已入队条目留待重放
                    logger.error(
                        "onebot_event_handle_failed",
                        error=str(e),
                        exc_info=True,
                        event_type=event.get("type") or event.get("post_type"),
                    )
                else:
                    if queue is not None and entry_id is not None:
                        try:
                            # 必须用 remove()（XACK+XDEL）：该条目未经 XREADGROUP 投递、
                            # 不在 PEL 里，只 ack 等于没处理，会被恢复循环重投（round-3 H3）
                            await queue.remove(entry_id)
                        except Exception as e:
                            logger.warning("onebot_event_remove_failed", error=str(e))
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

    async def handle_event(self, event: dict[str, Any], onebot_ws: WebSocket) -> None:
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
            await self._handle_meta_event(event, onebot_ws)
        elif event_type == "notice":
            logger.debug("onebot_notice_event_ignored", detail_type=event.get("detail_type"))
        elif event_type == "request":
            logger.debug("onebot_request_event_ignored", detail_type=event.get("detail_type"))
        else:
            logger.debug("onebot_unknown_event", event_type=event_type)

    async def _handle_message_event(self, event: dict[str, Any], onebot_ws: WebSocket) -> None:
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
        if not self_id:
            _warn_self_id_missing()

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

        # round-3 H5：排除机器人自身的消息。回显实现或第二个关键词机器人
        # 会与本角色互相触发回复形成乒乓死循环（问候层无概率闸门，必然互答）
        if user_id is not None and str(user_id) == self_id:
            logger.debug("onebot_self_message_skipped", self_id=self_id, group_id=group_id)
            return

        # R5-M7：入站限流在一切 LLM 路径之前——洪泛群每条消息都会触发
        # judge+reply 两次 LLM 调用，超限消息必须静默丢弃（不回复、不入环）
        chat_kind, chat_id = ("g", group_id) if is_group and group_id is not None else ("u", user_id)
        if not await self._check_inbound_rate_limit(chat_kind, chat_id):
            return

        message_id = str(event.get("message_id") or "")

        # R4-M14：群消息先读共享上下文环、再把当前消息入环（无论是否回复），
        # 使角色回复时能看到其他群成员的近期发言
        group_context: list[dict[str, str]] = []
        if is_group and group_id is not None:
            try:
                from src.runtime import get_redis

                group_redis = get_redis()
                if group_redis is not None:
                    group_context = await self._read_group_context(group_redis, str(group_id))
                    sender_name = str((event.get("sender") or {}).get("nickname") or user_id or "群友")
                    await self._record_group_message(group_redis, str(group_id), sender_name, raw_message)
            except Exception as e:
                logger.warning("onebot_group_context_failed", group_id=group_id, error=str(e))
            group_context = group_context or []

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
                should, reason = await self._should_reply_in_group(raw_message, user_id, onebot_ws, group_id=group_id)
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
                assert llm_client is not None and prompts_obj is not None
                svc = MessageService(
                    session=session,
                    llm=llm_client,
                    prompts=prompts_obj,
                    redis=redis_client,
                )
                result = await svc.handle_user_message(
                    character_id=character_id,
                    user_id=internal_user_id,
                    platform="qq",
                    content=raw_message,
                    group_context=group_context or None,
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

        # round-3 H4：去重认领放在「回复文本已生成、即将发送」处。群聊智能回复
        # 与私聊两条路径在此汇合，单点认领同时覆盖两者；若在处理开始就 SETNX，
        # 进程在生成与发送之间崩溃会把消息永久锁死（重放被去重挡住而回复未发出）。
        if message_id:
            claimed = await self._claim_reply_slot(message_id)
            if not claimed:
                logger.info(
                    "onebot_duplicate_reply_skipped",
                    message_id=message_id,
                    user_id=internal_user_id,
                )
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
            # 发送失败必须释放槽位，否则重放被去重挡住、回复永久丢失（round-3 H4）
            if message_id:
                await self._release_reply_slot(message_id)
            logger.error(
                "onebot_send_reply_failed",
                user_id=internal_user_id,
                error=str(e),
                exc_info=True,
            )

    async def _claim_reply_slot(self, message_id: str) -> bool:
        """认领回复槽位：SETNX 成功者才获得发送资格

        时序约束（round-3 H4）：必须在回复文本生成成功、即将发送时才认领，
        崩溃丢失窗口从整个处理流程压缩到发送本身。

        Redis 不可用时视为无去重层（与原行为一致），直接放行。
        """
        from src.runtime import get_redis

        redis_client = get_redis()
        if redis_client is None:
            return True
        return bool(await redis_client.set(_reply_dedup_key(message_id), "1", ex=_REPLY_DEDUP_TTL_SECONDS, nx=True))

    async def _release_reply_slot(self, message_id: str) -> None:
        """释放回复槽位：发送失败后调用，让重放路径可以重试发送（round-3 H4）"""
        from src.runtime import get_redis

        redis_client = get_redis()
        if redis_client is None:
            return
        await redis_client.delete(_reply_dedup_key(message_id))

    async def _check_inbound_rate_limit(self, chat_kind: str, chat_id: str | int | None) -> bool:
        """每会话入站固定窗口限流（R5-M7），返回 False 表示超限应静默丢弃

        复用 security.RateLimiter 的 INCR+EXPIRE 原子实现；Redis 不可用时与
        回复去重层同理放行（无 Redis 不改变消息语义，只失去保护层）。
        """
        limit = _get_rate_limit_per_minute()
        if limit <= 0 or chat_id is None:
            return True

        from src.runtime import get_redis
        from src.security.rate_limiter import RateLimiter

        redis_client = get_redis()
        if redis_client is None:
            return True
        limiter = RateLimiter(redis_client, key_prefix=f"onebot:rl:{chat_kind}")
        allowed = await limiter.check(str(chat_id), max_requests=limit, window_seconds=_RATE_LIMIT_WINDOW_SECONDS)
        if not allowed:
            logger.warning(
                "onebot_rate_limited",
                chat_kind=chat_kind,
                chat_id=str(chat_id),
                limit=limit,
                window_seconds=_RATE_LIMIT_WINDOW_SECONDS,
            )
        return allowed

    async def _handle_meta_event(self, event: dict[str, Any], onebot_ws: WebSocket) -> None:
        """处理 OneBot 元事件（心跳 / 生命周期）

        兼容 OneBot v12 (detail_type) 和 OneBot 11 (meta_event_type)。
        心跳时间戳与 self_id 归属在此记录（M14/M17），供连接选择与新鲜度判定使用。
        """
        detail_type = event.get("detail_type") or event.get("meta_event_type")

        # v11/v12 的元事件均携带 self_id：记录连接的账号归属，供按账号路由
        sid = event.get("self_id")
        if sid is not None:
            self._conn_self_id[onebot_ws] = str(sid)

        if detail_type == "heartbeat":
            self._last_heartbeat[onebot_ws] = time.monotonic()
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

    @staticmethod
    def _group_context_key(group_id: str) -> str:
        return f"onebot:group:{group_id}:recent"

    async def _record_group_message(self, redis: Any, group_id: str, sender: str, text: str) -> None:
        """群消息写入共享上下文环（R4-M14）

        LPUSH+LTRIM 保留最近 20 条，24h 过期；单条文本截断 200 字符控制体积。
        """
        entry = json.dumps({"sender": sender[:50], "text": text[:200]}, ensure_ascii=False)
        key = self._group_context_key(group_id)
        await redis.lpush(key, entry)
        await redis.ltrim(key, 0, _GROUP_CONTEXT_MAX_MESSAGES - 1)
        await redis.expire(key, _GROUP_CONTEXT_TTL_SECONDS)

    async def _read_group_context(self, redis: Any, group_id: str) -> list[dict[str, str]]:
        """读取群共享上下文环（旧→新顺序）；坏行跳过

        写入用 LPUSH（列表头=最新），LRANGE 天然最新在前，必须反转才满足
        docstring 契约——LLM 按时间序理解群聊，倒序上下文会让角色误读因果。
        """
        raw_items = await redis.lrange(self._group_context_key(group_id), 0, _GROUP_CONTEXT_MAX_MESSAGES - 1)
        out: list[dict[str, str]] = []
        for item in reversed(raw_items):
            try:
                text = item.decode("utf-8") if isinstance(item, bytes | bytearray) else str(item)
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    out.append({"sender": str(parsed.get("sender", "")), "text": str(parsed.get("text", ""))})
            except Exception:
                continue
        return out

    async def _should_reply_in_group(
        self,
        message: str,
        sender_user_id: str | int | None,
        onebot_ws: WebSocket,
        group_id: str | int | None = None,
    ) -> tuple[bool, str]:
        """群聊智能回复决策 - 调用 MessageService.should_reply_in_group

        流程：
        1. 获取 LLM 全局实例
        2. 解析角色 ID 和角色名（A-6：判定与回复必须解析到同一角色）
        3. 调用 MessageService.should_reply_in_group 判断是否回复

        Args:
            message: 群聊消息纯文本
            sender_user_id: 发送者 QQ 号
            onebot_ws: OneBot WebSocket 连接
            group_id: 群 ID（群-角色映射的键；缺省时仅默认角色兜底）

        Returns:
            (should_reply, reason)
        """
        llm_client, prompts_obj, redis_client = _get_llm_globals()
        if llm_client is None or prompts_obj is None:
            return False, "llm_not_ready"

        # 解析角色 ID（群聊场景）：必须带 group_id，否则群-角色映射永远失效，
        # 判定角色与实际回复角色可能不一致（A-6）
        character_id = _resolve_character_id(is_group=True, group_id=group_id)
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
                assert llm_client is not None and prompts_obj is not None
                svc = MessageService(
                    session=session,
                    llm=llm_client,
                    prompts=prompts_obj,
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
        # 出站净化（生成→QQ 链路安全边界）：
        # 1) 正文中的图片 URL 转为 [CQ:image] 使 QQ 渲染为图片
        # 2) 剥离其余全部 CQ 码——防止提示注入伪造 at/reply/JSON 等动作
        message = sanitize_outbound_qq_text(message)

        # 分段发送
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

        # R5-L9：群回复发送成功后写回共享上下文环——多角色同群时互相可见对方
        # 发言，否则各角色只能看到用户消息、会重复自问自答
        if event_type == "group":
            await self._record_bot_group_reply(group_id, message)

    async def _record_bot_group_reply(self, group_id: str | int | None, text: str) -> None:
        """角色群回复写入共享上下文环（R5-L9）

        记录失败必须就地吞掉：此时消息已发出，向上抛错会误触发回复槽位释放，
        重放路径会重发同一条回复。
        """
        if group_id is None:
            return
        character_id = _resolve_character_id(is_group=True, group_id=group_id)
        if character_id is None:
            # 未配置角色的系统兜底提示不属任何角色发言
            return
        try:
            from src.runtime import get_redis

            redis_client = get_redis()
            if redis_client is None:
                return
            sender = await self._resolve_character_name(character_id)
            await self._record_group_message(redis_client, str(group_id), sender, text)
        except Exception as e:
            logger.warning("onebot_bot_reply_record_failed", group_id=group_id, error=str(e))

    async def _resolve_character_name(self, character_id: UUID) -> str:
        """解析角色名用于环内 sender 标识；档案缺失时以 ID 代替保持可追溯"""
        async with db.session() as session:
            from src.db.repositories import CharacterRepository

            char_repo = CharacterRepository(session)
            character = await char_repo.get_by_id(character_id)
        return character.name if character is not None else str(character_id)

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
            params: dict[str, Any] = {
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

        # M13/M17：优先原连接，发送失败（已关闭/超时）降级到其余活跃连接。
        # 全部候选失败时向上抛出——回复槽位释放、push_share 的多连接轮询与
        # 错误提示路径都依赖异常感知彻底失败（round-3 H4 契约）。
        await self._send_with_failover(onebot_ws, lambda: action)
        logger.info(
            "onebot_message_sent",
            event_type=event_type,
            user_id=user_id,
            group_id=group_id,
            message_length=len(message),
            segment_index=segment_index,
            segment_total=segment_total,
        )

    async def _send_with_failover(
        self,
        preferred_ws: WebSocket,
        build_payload_fn: Callable[[], dict[str, Any]],
    ) -> None:
        """优先经 preferred_ws 发送一帧，失败时降级到其余活跃连接

        失败判定：starlette RuntimeError（发送中连接关闭）与 TimeoutError
        （半开连接发送超时）。失败连接立即驱逐，避免后续选择再次命中。

        Raises:
            RuntimeError: 无任何 CONNECTED 候选，或全部候选发送失败
                （重抛最后一个错误以保留原始失败语义）
            Exception: 非连接类发送异常原样上抛
        """
        connected = await self._connected_snapshot()
        ordered = [preferred_ws] + [ws for ws in connected if ws is not preferred_ws]

        last_error: Exception | None = None
        for ws in ordered:
            if ws.client_state != WebSocketState.CONNECTED:
                continue
            try:
                await self._send_json(ws, build_payload_fn())
            except (RuntimeError, TimeoutError) as e:
                last_error = e
                logger.warning("onebot_send_failover_try_next", error=str(e))
                await self._evict_connection(ws, f"send_failed:{type(e).__name__}")
            else:
                return

        if last_error is None:
            raise RuntimeError("onebot_no_connected_connection")
        raise last_error

    async def _send_json(self, onebot_ws: WebSocket, payload: dict[str, Any]) -> None:
        """带超时的出站帧发送（M17：半开连接的 send_text 可能无限阻塞）"""
        await asyncio.wait_for(
            onebot_ws.send_text(json.dumps(payload, ensure_ascii=False)),
            timeout=_SEND_TIMEOUT_SECONDS,
        )

    async def _evict_connection(self, onebot_ws: WebSocket, reason: str) -> None:
        """驱逐发送失败的连接及其心跳/账号记录，避免再次被选中"""
        async with self._lock:
            if onebot_ws not in self._connections:
                return
            self._connections.discard(onebot_ws)
            # 与 _unregister 同理：元事件未到的连接允许无记录
            self._last_heartbeat.pop(onebot_ws, None)
            self._conn_self_id.pop(onebot_ws, None)
        logger.warning("onebot_connection_evicted", reason=reason)

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

        # 获取活跃连接（此前取 conns[0] 单点发送，任一连接异常即整体失败）
        async with self._lock:
            conns = [ws for ws in self._connections if ws.client_state == WebSocketState.CONNECTED]
        if not conns:
            logger.warning(
                "onebot_push_share_no_connection",
                user_id=user_id,
                group_id=group_id,
            )
            return False

        # 依次尝试各连接：多实现（如多个 QQ 号）反连时避免绑死在任意一个上
        last_error: Exception | None = None
        for ws in conns:
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
                last_error = e
                logger.warning("onebot_push_share_send_failed_try_next", error=str(e))

        logger.error(
            "onebot_push_share_failed",
            user_id=user_id,
            group_id=group_id,
            error=str(last_error),
            exc_info=last_error is not None,
        )
        return False


def _get_at_only() -> bool:
    """从配置读取群聊是否仅在被 @ 时回复"""
    from src.config import settings

    return settings.onebot_group_at_only


def _get_rate_limit_per_minute() -> int:
    """从配置读取单会话每分钟入站消息上限（0=禁用）"""
    from src.config import settings

    return settings.onebot_rate_limit_per_minute


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
