import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth";
import { queryKeys } from "@/lib/queries";

export type ChatSocketStatus = "idle" | "connecting" | "open" | "closed";

interface ReplyFrame {
  type: "reply";
  content: string;
  conversation_id: string;
  message_id: string;
  tokens: number;
  cost: number;
}

const MAX_RETRIES = 10;

/**
 * P0-3：订阅 /ws/chat/{characterId}，回复帧到达即失效 messages 缓存。
 * 后端协议（messaging/websocket.py）：
 * - 入站：{"type":"message","content":"..."}
 * - 出站：connected / reply / error
 * token 经 Sec-WebSocket-Protocol 子协议传递（与 useDashboardSocket 同约定），
 * sub 必须与 user_id 查询参数一致，否则服务端 1008 拒绝。
 */
export function useChatSocket(characterId: string | undefined) {
  const queryClient = useQueryClient();
  const token = useAuthStore((s) => s.token);
  const userId = useAuthStore((s) => s.userId);
  const [status, setStatus] = useState<ChatSocketStatus>("idle");
  const wsRef = useRef<WebSocket | null>(null);
  const qcRef = useRef(queryClient);
  qcRef.current = queryClient;

  useEffect(() => {
    if (!characterId || !isAuthenticatedReady(token, userId)) {
      setStatus("idle");
      return;
    }

    let disposed = false;
    let retryCount = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let ws: WebSocket | null = null;

    const clearRetryTimer = () => {
      if (retryTimer) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
    };

    const connect = () => {
      if (disposed) return;
      setStatus("connecting");
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${window.location.host}/ws/chat/${characterId}?user_id=${encodeURIComponent(userId ?? "")}&platform=web`;
      ws = new WebSocket(url, ["bearer", token ?? ""]);
      wsRef.current = ws;

      ws.onopen = () => {
        retryCount = 0;
        setStatus("open");
      };

      ws.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data as string) as ReplyFrame;
          if (frame.type === "reply") {
            qcRef.current.invalidateQueries({ queryKey: queryKeys.messages(characterId) });
            qcRef.current.invalidateQueries({ queryKey: queryKeys.conversations });
          }
        } catch {
          // 非 JSON 帧忽略
        }
      };

      ws.onclose = () => {
        if (disposed) return;
        setStatus("closed");
        if (retryCount < MAX_RETRIES) {
          const delay = Math.min(1000 * 2 ** retryCount, 30000);
          retryCount += 1;
          retryTimer = setTimeout(connect, delay);
        }
      };
    };

    connect();

    return () => {
      disposed = true;
      clearRetryTimer();
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      wsRef.current = null;
    };
  }, [characterId, token, userId]);

  const send = (content: string): boolean => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type: "message", content }));
    return true;
  };

  return { status, send };
}

function isAuthenticatedReady(token: string | null, userId: string | null): boolean {
  return Boolean(token && userId);
}
