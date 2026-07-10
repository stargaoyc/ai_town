import { useCallback, useEffect, useRef, useState } from "react";

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

interface UseWebSocketOptions {
  url: string;
  onMessage?: (data: unknown) => void;
  onOpen?: () => void;
  onClose?: () => void;
  maxRetries?: number;
}

interface UseWebSocketReturn {
  status: ConnectionStatus;
  retryCount: number;
  send: (data: string) => void;
  connect: () => void;
  disconnect: () => void;
}

export function useWebSocket({
  url,
  onMessage,
  onOpen,
  onClose,
  maxRetries = 10,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [retryCount, setRetryCount] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryCountRef = useRef(0);
  const shouldConnectRef = useRef(false);

  // 保持回调引用最新
  const callbacksRef = useRef({ onMessage, onOpen, onClose });
  callbacksRef.current = { onMessage, onOpen, onClose };

  const clearRetryTimer = useCallback(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    shouldConnectRef.current = true;
    clearRetryTimer();

    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    setStatus("connecting");

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        retryCountRef.current = 0;
        setRetryCount(0);
        setStatus("connected");
        callbacksRef.current.onOpen?.();
      };

      ws.onmessage = (event) => {
        try {
          const data = event.data as string;
          const parsed = JSON.parse(data);
          callbacksRef.current.onMessage?.(parsed);
        } catch {
          callbacksRef.current.onMessage?.(event.data);
        }
      };

      ws.onerror = () => {
        setStatus("error");
      };

      ws.onclose = () => {
        setStatus("disconnected");
        callbacksRef.current.onClose?.();

        // 自动重连
        if (shouldConnectRef.current && retryCountRef.current < maxRetries) {
          const delay = Math.min(1000 * 2 ** retryCountRef.current, 30000);
          retryCountRef.current += 1;
          setRetryCount(retryCountRef.current);

          retryTimerRef.current = setTimeout(() => {
            if (shouldConnectRef.current) {
              connect();
            }
          }, delay);
        }
      };
    } catch {
      setStatus("error");
    }
  }, [url, maxRetries, clearRetryTimer]);

  const disconnect = useCallback(() => {
    shouldConnectRef.current = false;
    clearRetryTimer();
    retryCountRef.current = 0;
    setRetryCount(0);

    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus("disconnected");
  }, [clearRetryTimer]);

  const send = useCallback((data: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  useEffect(() => {
    return () => {
      shouldConnectRef.current = false;
      clearRetryTimer();
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [clearRetryTimer]);

  return { status, retryCount, send, connect, disconnect };
}
