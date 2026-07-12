const BASE_URL = "/api/v1";

function getToken(): string | null {
  return localStorage.getItem("token");
}

function getApiKey(): string | null {
  return localStorage.getItem("api_key");
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
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
        window.location.href = "/login";
      }
      throw new Error("未认证，请重新登录");
    }
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export interface Character {
  id: string;
  name: string;
  age?: number;
  occupation?: string;
  is_active: boolean;
  traits?: Record<string, unknown>;
  backstory?: string;
  avatar_url?: string;
  state?: CharacterState;
}

export interface CharacterState {
  location?: string;
  stamina: number;
  satiety: number;
  mood?: string;
  money: number;
  phone_battery: number;
  social_energy: number;
  current_action?: Record<string, unknown> | null;
  version: number;
}

export interface WorldState {
  tick_id: number;
  world_time: string;
  weather: string;
  temperature?: number;
  active_characters: number;
}

export interface Action {
  id: string;
  name: string;
  description?: string;
  category: string;
}

export interface Memory {
  id: string;
  character_id: string;
  content: string;
  importance: number;
  timestamp: string;
  is_reflected: boolean;
  source_type: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  sender: "user" | "character" | "system";
  content: string;
  tokens?: number;
  cost?: number;
  created_at: string;
}

export interface Conversation {
  id: string;
  character_id: string;
  user_id: string;
  platform: string;
  last_message_at: string;
}

export interface AdminStatus {
  redis: string;
  world_engine: { running: boolean; tick_id: number; is_leader: boolean };
  character_engine: { available: boolean; tick_interval: number };
  action_registry: { initialized: boolean; action_count: number };
  llm: { initialized: boolean; model: string };
}

export interface Scene {
  id: string;
  name: string;
  description?: string;
  type?: string;
  capacity?: number;
  crowdedness?: number;
  characters_present?: string[];
}

export const api = {
  getHealth: () =>
    fetch("/health").then((r) => r.json()) as Promise<{
      status: string;
      world_tick: number;
      redis: string;
    }>,

  getCharacters: (params?: { limit?: number; active_only?: boolean }) => {
    const qs = params
      ? "?" + new URLSearchParams(params as Record<string, string>).toString()
      : "";
    return request<{ data: Character[]; total: number }>(`/characters${qs}`);
  },
  getCharacter: (id: string) =>
    request<{ character: Character; state: Partial<CharacterState> }>(
      `/characters/${id}`,
    ).then((res) => ({ ...res.character, state: res.state })),

  getWorld: () => request<WorldState>("/world"),
  getWorldEvents: (tickId: number) =>
    request<{ data: unknown[] }>(`/world/events/${tickId}`),

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
    request<{ data: Message[] }>(
      `/characters/${characterId}/messages?limit=${limit}`,
    ),
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
      Object.entries(params).reduce((acc, [k, v]) => {
        if (v !== undefined && v !== null) acc[k] = String(v);
        return acc;
      }, {} as Record<string, string>),
    ).toString();
    return request<{ data: WorldEventEntry[]; total: number }>(
      `/world/events?${qs}`,
    );
  },

  // 反思
  getReflections: (characterId: string) =>
    request<{ data: ReflectionEntry[] }>(
      `/characters/${characterId}/reflections`,
    ),

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

  // QQ 消息监控
  getOnebotMessages: (limit = 50) =>
    request<{ data: OnebotMessageEntry[]; total: number }>(
      `/admin/onebot/messages?limit=${limit}`,
    ),

  // 主动分享历史
  getProactiveShares: (limit = 50) =>
    request<{ data: ShareEntry[]; total: number }>(
      `/admin/proactive-shares?limit=${limit}`,
    ),

  // 向量检索测试
  vectorSearch: (characterId: string, query: string, topK = 10) =>
    request<{ data: VectorSearchResult[]; total: number; query: string }>(
      `/admin/vector-search?character_id=${characterId}&query=${encodeURIComponent(query)}&top_k=${topK}`,
      { method: "POST" },
    ),

  // 世界快照
  getWorldSnapshots: (limit = 20) =>
    request<{ data: SnapshotEntry[]; total: number }>(
      `/admin/world/snapshots?limit=${limit}`,
    ),

  // 消息统计
  getMessageStats: (params?: {
    character_id?: string;
    start_date?: string;
    end_date?: string;
  }) => {
    const qs = params
      ? "?" + new URLSearchParams(params as Record<string, string>).toString()
      : "";
    return request<MessageStats>(`/messages/stats${qs}`);
  },

  // 模块列表
  getModules: () => request<{ data: ModuleEntry[]; total: number }>("/modules"),

  // MCP 服务器
  getMcpServers: () => request<{ data: McpServerEntry[] }>("/mcp/servers"),
  getMcpTools: () => request<{ data: McpToolEntry[] }>("/mcp/tools"),
};

// ===== 扩展类型定义 =====

export interface StateHistoryEntry {
  stamina: number;
  satiety: number;
  mood: string;
  money: number;
  phone_battery: number;
  social_energy: number;
  location: string;
  updated_at: string;
}

export interface WorldEventEntry {
  id: string;
  tick_id: number;
  event_type: string;
  event_key: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ReflectionEntry {
  id: string;
  character_id: string;
  content: string;
  created_at: string;
}

export interface PlanEntry {
  id: string;
  character_id: string;
  title: string;
  description?: string;
  type?: string;
  status: string;
  priority?: number;
  progress?: number;
  deadline?: string | null;
  created_at: string;
  updated_at?: string;
}

export interface ActionEntry {
  id: string;
  character_id?: string;
  action_id: string;
  action_name?: string;
  params?: Record<string, unknown>;
  reason?: string;
  result?: Record<string, unknown> | null;
  duration_minutes?: number;
  duration?: number;
  location?: string;
  related_characters?: string[];
  timestamp?: string;
  created_at?: string;
}

export interface RelationEntry {
  target_id: string;
  target_name?: string;
  relation_type: string;
  relationship_type?: string;
  trust: number;
  intimacy: number;
  strength: number;
  last_interaction_at?: string;
  notes?: string;
}

export interface OnebotMessageEntry {
  message_id: string;
  conversation_id: string;
  character_id: string;
  user_id: string;
  sender: string;
  content: string;
  tokens?: number;
  cost?: number;
  created_at: string;
}

export interface ShareEntry {
  message_id: string;
  conversation_id: string;
  sender: string;
  content: string;
  tokens?: number;
  cost?: number;
  created_at: string;
}

export interface VectorSearchResult {
  id: string;
  content: string;
  importance: number;
  timestamp: string;
  similarity: number;
  is_reflected: boolean;
  source_type: string;
}

export interface SnapshotEntry {
  id: string;
  tick_id: number;
  state: Record<string, unknown>;
  created_at: string;
}

export interface MessageStats {
  total_messages: number;
  total_tokens: number;
  total_cost: number;
  by_character?: Record<string, { messages: number; tokens: number; cost: number }>;
  by_day?: Record<string, { messages: number; tokens: number; cost: number }>;
}

export interface ModuleEntry {
  name: string;
  type: string;
  status: string;
  description: string;
}

export interface McpServerEntry {
  name: string;
  type: string;
  description?: string;
  status?: string;
}

export interface McpToolEntry {
  name: string;
  server: string;
  server_type: string;
}
