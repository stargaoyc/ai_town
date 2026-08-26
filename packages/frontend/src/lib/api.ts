import type {
  Action,
  ActionEntry,
  AdminStatus,
  AppNotification,
  Character,
  CharacterState,
  Conversation,
  DetailedMetrics,
  DiaryEntry,
  LogEntry,
  McpServerEntry,
  McpToolEntry,
  Memory,
  Message,
  MessageStats,
  ModuleEntry,
  NearbyCharacterEntry,
  OnebotMessageEntry,
  PersonMemoryEntry,
  PlanEntry,
  ReflectionEntry,
  RelationEntry,
  Scene,
  ShareEntry,
  SnapshotEntry,
  StateHistoryEntry,
  VectorSearchResult,
  WorldEventEntry,
  WorldState,
} from "./api-types";

const BASE_URL = "/api/v1";

function getToken(): string | null {
  return localStorage.getItem("token");
}

function getApiKey(): string | null {
  return localStorage.getItem("api_key");
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const apiKey = getApiKey();
  if (apiKey) headers["X-API-Key"] = apiKey;

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    // 401 未认证：清除 token 并跳转登录页
    if (res.status === 401) {
      localStorage.removeItem("token");
      localStorage.removeItem("user_id");
      if (window.location.pathname !== "/login") {
        // api.ts 位于 React 之外，无法用 router 导航；location.replace 避免
        // 回退键回到已失效的会话页
        window.location.replace("/login");
      }
      throw new Error("未认证，请重新登录");
    }
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  getHealth: () =>
    fetch("/health").then((r) => r.json()) as Promise<{
      status: string;
      world_tick: number;
      redis: string;
      degraded_reasons?: string[];
    }>,

  getCharacters: (params?: { limit?: number; active_only?: boolean }) => {
    const qs = params ? "?" + new URLSearchParams(params as Record<string, string>).toString() : "";
    return request<{ data: Character[]; total: number }>(`/characters${qs}`);
  },
  getCharacter: (id: string) =>
    request<{ character: Character; state: Partial<CharacterState> }>(`/characters/${id}`).then(
      (res): Character => ({ ...res.character, state: res.state }),
    ),

  getWorld: () => request<WorldState>("/world"),
  getWorldEvents: (tickId: number) => request<{ data: unknown[] }>(`/world/events/${tickId}`),

  getActions: () => request<{ data: Action[] }>("/actions"),
  getMemories: (characterId: string, limit = 20) =>
    request<{ data: Memory[] }>(`/memories/${characterId}?limit=${limit}`),

  sendMessage: (characterId: string, userId: string, content: string) =>
    request<{
      data: {
        conversation_id: string;
        message_id: string | null;
        content: string;
        tokens: number | null;
        cost: number | null;
        error: string | null;
      };
    }>("/messages/send", {
      method: "POST",
      body: JSON.stringify({
        character_id: characterId,
        user_id: userId,
        content,
      }),
    }),
  getHistory: (characterId: string, limit = 20) =>
    request<{ data: Message[] }>(`/characters/${characterId}/messages?limit=${limit}`),
  getConversations: () => request<{ data: Conversation[] }>("/conversations"),

  forceTick: () => request("/admin/tick", { method: "POST" }),
  getAdminStatus: () => request<AdminStatus>("/admin/status"),

  getScenes: () => request<{ data: Scene[] }>("/town/scenes"),
  getScene: (id: string) => request<Scene>(`/town/scenes/${id}`),

  // ===== 扩展 API（新功能） =====

  // 角色导入
  importCharacter: (yaml: string) =>
    request("/admin/characters/import", {
      method: "POST",
      body: JSON.stringify({ yaml }),
    }),
  importCharacterBatch: (yaml: string) =>
    request("/admin/characters/import-batch", {
      method: "POST",
      body: JSON.stringify({ yaml }),
    }),

  // 角色删除
  deleteCharacter: (characterId: string) =>
    request<{ success: boolean; message: string; character_id: string }>(
      `/admin/characters/${characterId}`,
      { method: "DELETE" },
    ),

  // 角色状态历史
  getCharacterStateHistory: (id: string, limit = 50) =>
    request<{ data: StateHistoryEntry[]; total: number }>(
      `/characters/${id}/state-history?limit=${limit}`,
    ),

  // 世界事件范围查询
  getWorldEventsRange: (params: {
    start_tick?: number;
    end_tick?: number;
    event_type?: string;
    limit?: number;
  }) => {
    const qs = new URLSearchParams(
      Object.entries(params).reduce(
        (acc, [k, v]) => {
          if (v !== undefined && v !== null) acc[k] = String(v);
          return acc;
        },
        {} as Record<string, string>,
      ),
    ).toString();
    return request<{ data: WorldEventEntry[]; total: number }>(`/world/events?${qs}`);
  },

  // 反思
  getReflections: (characterId: string) =>
    request<{ data: ReflectionEntry[] }>(`/characters/${characterId}/reflections`),

  // 规划
  getPlans: (characterId: string) =>
    request<{ data: PlanEntry[] }>(`/characters/${characterId}/plans`),

  // 角色行为日志
  getCharacterActions: (characterId: string, limit = 50) =>
    request<{ data: ActionEntry[]; total: number }>(
      `/characters/${characterId}/actions?limit=${limit}`,
    ),

  // 角色关系
  getRelations: (characterId: string) =>
    request<{ data: RelationEntry[] }>(`/characters/${characterId}/relations`),

  // 同场景其他角色（多智能体交互可见性）
  getNearbyCharacters: (characterId: string) =>
    request<{ data: NearbyCharacterEntry[]; total: number; location: string | null }>(
      `/characters/${characterId}/nearby`,
    ),

  // QQ 消息监控
  getOnebotMessages: (limit = 50) =>
    request<{ data: OnebotMessageEntry[]; total: number }>(`/admin/onebot/messages?limit=${limit}`),

  // 主动分享历史
  getProactiveShares: (limit = 50) =>
    request<{ data: ShareEntry[]; total: number }>(`/admin/proactive-shares?limit=${limit}`),

  // 向量检索测试（characterId 为 null 时执行跨角色全局检索）
  vectorSearch: (characterId: string | null, query: string, topK = 10) => {
    const scopeParam = characterId ? `character_id=${characterId}&` : "";
    return request<{ data: VectorSearchResult[]; total: number; query: string }>(
      `/admin/vector-search?${scopeParam}query=${encodeURIComponent(query)}&top_k=${topK}`,
      { method: "POST" },
    );
  },

  // 世界快照
  getWorldSnapshots: (limit = 20) =>
    request<{ data: SnapshotEntry[]; total: number }>(`/admin/world/snapshots?limit=${limit}`),

  // 消息统计
  getMessageStats: (params?: { character_id?: string; start_date?: string; end_date?: string }) => {
    const qs = params ? "?" + new URLSearchParams(params as Record<string, string>).toString() : "";
    return request<MessageStats>(`/messages/stats${qs}`);
  },

  // 模块列表
  getModules: () => request<{ data: ModuleEntry[]; total: number }>("/modules"),

  // 工具命名空间
  getMcpServers: () => request<{ data: McpServerEntry[] }>("/tools/servers"),
  getMcpTools: () => request<{ data: McpToolEntry[] }>("/tools/tools"),
  getMcpServersHealth: () =>
    request<{
      data: Array<{
        name: string;
        endpoint: string;
        status: "online" | "offline";
        latency_ms: number;
        http_status: number | null;
      }>;
      total: number;
      online: number;
      offline: number;
    }>("/tools/servers/health"),
  invokeMcpTool: (toolName: string, serverName: string, args: Record<string, unknown>) =>
    request<{
      success: boolean;
      status_code?: number;
      result?: unknown;
      error?: string;
      endpoint: string;
    }>(`/tools/tools/${toolName}/invoke?server_name=${serverName}`, {
      method: "POST",
      body: JSON.stringify(args),
    }),
  toggleMcpServer: (serverName: string, enabled: boolean) =>
    request<{
      success: boolean;
      server: string;
      enabled: boolean;
    }>(`/tools/servers/${serverName}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),

  // 系统日志
  getLogs: (lines = 100, level?: string) => {
    const qs = new URLSearchParams({
      lines: String(lines),
      ...(level ? { level } : {}),
    }).toString();
    return request<{ data: LogEntry[]; total: number; source: string }>(`/admin/logs?${qs}`);
  },

  // 详细指标
  getDetailedMetrics: () => request<{ data: DetailedMetrics }>("/admin/metrics-detail"),

  // 运行时配置
  getConfig: () =>
    request<{
      data: Array<{
        key: string;
        label: string;
        type: string;
        default: unknown;
        current: unknown;
        overridden: boolean;
      }>;
      total: number;
    }>("/admin/config"),
  updateConfig: (updates: Record<string, unknown>) =>
    request<{ success: boolean; updated: number; data: unknown[] }>("/admin/config", {
      method: "PUT",
      body: JSON.stringify(updates),
    }),
  resetConfig: (key: string) =>
    request<{ success: boolean; key: string; reset_to: unknown }>(`/admin/config/${key}`, {
      method: "DELETE",
    }),

  // 通知中心
  getNotifications: (limit = 50, unreadOnly = false) => {
    const qs = new URLSearchParams({
      limit: String(limit),
      unread_only: String(unreadOnly),
    }).toString();
    return request<{
      data: AppNotification[];
      total: number;
      unread: number;
    }>(`/notifications?${qs}`);
  },
  createNotification: (type: string, title: string, content: string) =>
    request<{ data: AppNotification }>("/notifications", {
      method: "POST",
      body: JSON.stringify({ type, title, content }),
    }),
  markNotificationRead: (id: string) =>
    request<{ success: boolean; id: string }>(`/notifications/${id}/read`, {
      method: "PUT",
    }),
  markAllNotificationsRead: () =>
    request<{ success: boolean; updated: number }>("/notifications/read-all", {
      method: "PUT",
    }),
  deleteNotification: (id: string) =>
    request<{ success: boolean; id: string }>(`/notifications/${id}`, {
      method: "DELETE",
    }),
  clearAllNotifications: () =>
    request<{ success: boolean }>("/notifications", { method: "DELETE" }),

  // ===== 日记系统 =====
  getDiaries: (characterId: string, params?: { period?: string; limit?: number }) => {
    const qs = new URLSearchParams(
      Object.entries(params || {}).reduce(
        (acc, [k, v]) => {
          if (v !== undefined && v !== null) acc[k] = String(v);
          return acc;
        },
        {} as Record<string, string>,
      ),
    ).toString();
    return request<{ data: DiaryEntry[]; total: number }>(
      `/characters/${characterId}/diaries${qs ? "?" + qs : ""}`,
    );
  },
  generateDiary: (characterId: string, period: string, characterName = "") =>
    request<{ data: DiaryEntry }>(
      `/characters/${characterId}/diaries/generate?period=${period}&character_name=${encodeURIComponent(characterName)}`,
      { method: "POST" },
    ),

  // ===== 角色对用户的记忆 =====
  getPersonMemory: (characterId: string, userId: string) =>
    request<{ data: PersonMemoryEntry | null; exists: boolean }>(
      `/characters/${characterId}/person-memory?user_id=${encodeURIComponent(userId)}`,
    ),
  listPersonMemories: (characterId: string, limit = 50) =>
    request<{ data: PersonMemoryEntry[]; total: number }>(
      `/characters/${characterId}/person-memory/list?limit=${limit}`,
    ),
};

// P1-16：契约类型拆分至 api-types.ts，统一 re-export 保持 `from "@/lib/api"` 兼容
export type {
  SchemaPath,
  Character,
  CharacterState,
  WorldState,
  Action,
  Memory,
  Message,
  Conversation,
  AdminStatus,
  Scene,
  StateHistoryEntry,
  WorldEventEntry,
  ReflectionEntry,
  PlanEntry,
  ActionEntry,
  RelationEntry,
  NearbyCharacterEntry,
  OnebotMessageEntry,
  ShareEntry,
  VectorSearchResult,
  SnapshotEntry,
  MessageStats,
  ModuleEntry,
  McpServerEntry,
  McpToolEntry,
  LogEntry,
  AppNotification,
  DetailedMetrics,
  DiaryEntry,
  PersonMemoryEntry,
} from "./api-types";
