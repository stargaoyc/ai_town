import type { components, paths } from "@/types/api-generated";

// P1-16：API 契约类型集中地（从 api.ts 拆出）。
// api.ts 统一 re-export 本文件全部类型，既有 `from "@/lib/api"` 导入路径不变。
// 手写 interface 为临时边界：后端补齐 components.schemas 后应替换为生成引用。

export type SchemaPath<P extends keyof paths> = paths[P];

export interface Character {
  id: string;
  name: string;
  age?: number;
  occupation?: string;
  is_active: boolean;
  traits?: Record<string, unknown>;
  backstory?: string;
  avatar_url?: string;
  state?: Partial<CharacterState>;
}

export type CharacterState = components["schemas"]["CharacterStateOut"];

export type WorldState = components["schemas"]["WorldStateOut"];

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

export type Message = components["schemas"]["MessageOut"];

export type Conversation = components["schemas"]["ConversationOut"];

export interface AdminStatus {
  redis: string;
  world_engine: { running: boolean; tick_id: number; is_leader: boolean };
  character_engine: { available: boolean; tick_interval: number };
  action_registry: { initialized: boolean; action_count: number };
  llm: { initialized: boolean; model: string };
}

export type Scene = components["schemas"]["SceneOut"];

export type StateHistoryEntry = components["schemas"]["StateHistoryPointOut"];

export type WorldEventEntry = components["schemas"]["WorldEventEntryOut"];

export type ReflectionEntry = components["schemas"]["ReflectionOut"];

export type PlanEntry = components["schemas"]["PlanOut"];

export type ActionEntry = components["schemas"]["ActionRecordOut"];

export type RelationEntry = components["schemas"]["RelationOut"];

export type NearbyCharacterEntry = components["schemas"]["NearbyCharacterOut"];

export type OnebotMessageEntry = components["schemas"]["OnebotMessageEntryOut"];

export type ShareEntry = components["schemas"]["ShareEntryOut"];

export type VectorSearchResult = components["schemas"]["_VectorSearchItemOut"];

export type SnapshotEntry = components["schemas"]["SnapshotEntryOut"];

export type MessageStats = components["schemas"]["MessageStatsOut"];

export type ModuleEntry = components["schemas"]["ModuleEntryOut"];

export interface McpServerEntry {
  name: string;
  type: string;
  description?: string;
  status?: string;
  enabled?: boolean;
}

export interface McpToolEntry {
  name: string;
  server: string;
  server_type: string;
}

export interface LogEntry {
  timestamp?: string;
  level?: string;
  event?: string;
  [key: string]: unknown;
}

export type AppNotification = components["schemas"]["AppNotificationOut"];

export interface DetailedMetrics {
  world: {
    tick_total?: number;
    errors_total?: number;
    current_tick_id?: number;
    duration_sum?: number;
    duration_count?: number;
  };
  characters: {
    tick_total?: number;
    by_character?: Record<string, number>;
    errors_by_character?: Record<string, number>;
  };
  actions: {
    by_action?: Record<string, { success: number; failed: number }>;
  };
  llm: {
    cost_total_usd?: number;
    tokens_total?: number;
    calls_total?: number;
    calls?: Record<string, { success: number; failed: number }>;
    tokens?: Record<string, { prompt: number; completion: number }>;
  };
  messages: {
    by_platform?: Record<string, { success: number; failed: number }>;
  };
  system: {
    active_characters?: number;
    redis_connected?: number;
  };
  http: {
    requests?: Record<string, { total: number; by_status: Record<string, number> }>;
  };
}

export type DiaryEntry = components["schemas"]["DiaryOut"];

export type PersonMemoryEntry = components["schemas"]["PersonMemoryRecordOut"];
