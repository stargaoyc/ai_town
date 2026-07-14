"""临时测试：模拟 OneBot 11 私聊消息"""

import asyncio
import json
import sys

import websockets


async def test():
    port = sys.argv[1] if len(sys.argv) > 1 else "8002"
    url = f"ws://localhost:{port}/ws/onebot/v12"
    async with websockets.connect(url) as ws:
        print(f"Connected to {url}")
        # OneBot 11 私聊消息
        event = {
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "user_id": 203553391,
            "self_id": 123456,
            "message_id": "msg_001",
            "raw_message": "hello",
            "message": [{"type": "text", "data": {"text": "hello"}}],
            "time": 1700000000,
        }
        await ws.send(json.dumps(event))
        print("Sent OneBot 11 message")
        # 等待回复（最多 60 秒）
        try:
            reply = await asyncio.wait_for(ws.recv(), timeout=60)
            safe = reply.decode("utf-8", errors="replace") if isinstance(reply, bytes) else reply
            print(f"Reply ({len(reply)} bytes): {safe[:800]}")
        except TimeoutError:
            print("Timeout - no reply in 60s")


asyncio.run(test())
