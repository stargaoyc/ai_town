import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth";

type DashboardMessage = {
  type: "dashboard";
  world: {
    tick_id: number;
    world_time: string;
    weather: string;
    temperature: number | null;
  };
  notifications_unread: number;
};

const MAX_RETRIES = 10;

/**
 * 订阅 /ws/dashboard 实时推送，收到帧即失效对应 react-query 缓存，
 * 让 useWorld/useHealth/useNotifications 等立即重取最新数据。
 *
 * 服务端每 5 秒推一帧（仅在有订阅者时采集），取代前端各自 5s/10s 轮询。
 * 断线自动指数退避重连；未登录时不建立连接。
 */
export function useDashboardSocket() {
  const queryClient = useQueryClient();
  const token = useAuthStore((s) => s.token);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  // 用 ref 持有 queryClient，避免把不稳定的依赖放进 connect 闭包
  const qcRef = useRef(queryClient);
  qcRef.current = queryClient;
  const lastUnreadRef = useRef<number | null>(null);

  useEffect(() => {
    if (!isAuthenticated || !token) return;

    let ws: WebSocket | null = null;
    let retryCount = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const clearRetryTimer = () => {
      if (retryTimer) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
    };

    const applyFrame = (msg: DashboardMessage) => {
      const worldChanged = qcRef.current.getQueryData(["world"]) !== undefined;
      if (worldChanged) {
        qcRef.current.setQueryData(["world"], (old: unknown) =>
          old && typeof old === "object"
            ? {
                ...old,
                tick_id: msg.world.tick_id,
                world_time: msg.world.world_time,
                weather: msg.world.weather,
                temperature: msg.world.temperature ?? (old as { temperature?: number }).temperature,
              }
            : old,
        );
      }
      qcRef.current.invalidateQueries({ queryKey: ["health"] });

      if (lastUnreadRef.current !== null && lastUnreadRef.current !== msg.notifications_unread) {
        qcRef.current.invalidateQueries({ queryKey: ["notifications"] });
      }
      lastUnreadRef.current = msg.notifications_unread;
    };

    const connect = () => {
      if (disposed) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${window.location.host}/ws/dashboard`;
      // R4-L8：token 经 Sec-WebSocket-Protocol 子协议传递（服务端约定
      // "bearer, <token>"），不再拼进 URL——访问日志/代理不会记录凭据
      ws = new WebSocket(url, ["bearer", token]);

      // 连接成功即复位重试计数：否则多次偶发断线会累计退避，
      // 让后续真正的断线在几次内耗尽 MAX_RETRIES 后永久放弃
      ws.onopen = () => {
        retryCount = 0;
        console.debug("[ws/dashboard] connected");
      };

      ws.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data as string) as DashboardMessage;
          if (parsed.type === "dashboard") applyFrame(parsed);
        } catch {
          // 非 JSON 帧忽略
        }
      };

      ws.onclose = () => {
        if (disposed) return;
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
    };
  }, [isAuthenticated, token]);
}
