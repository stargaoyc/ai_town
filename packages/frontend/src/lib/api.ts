const BASE_URL = '/api/v1';

function getToken(): string | null {
  return localStorage.getItem('token');
}

function getApiKey(): string | null {
  return localStorage.getItem('api_key');
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const apiKey = getApiKey();
  if (apiKey) headers['X-API-Key'] = apiKey;

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!res.ok) {
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
  sender: 'user' | 'character' | 'system';
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
  world_engine: string;
  character_engine: string;
  redis: string;
  tick_id: number;
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
    fetch('/health').then((r) => r.json()) as Promise<{ status: string; world_tick: number; redis: string }>,

  getCharacters: (params?: { limit?: number; active_only?: boolean }) => {
    const qs = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
    return request<{ data: Character[]; total: number }>(`/characters${qs}`);
  },
  getCharacter: (id: string) => request<Character>(`/characters/${id}`),

  getWorld: () => request<WorldState>('/world'),
  getWorldEvents: (tickId: number) => request<{ data: unknown[] }>(`/world/events/${tickId}`),

  getActions: () => request<{ data: Action[] }>('/actions'),
  getMemories: (characterId: string, limit = 20) =>
    request<{ data: Memory[] }>(`/memories/${characterId}?limit=${limit}`),

  sendMessage: (characterId: string, userId: string, content: string) =>
    request<{ conversation_id: string; message_id: string; content: string }>('/messages/send', {
      method: 'POST',
      body: JSON.stringify({ character_id: characterId, user_id: userId, content }),
    }),
  getHistory: (characterId: string, limit = 20) =>
    request<{ data: Message[] }>(`/messages/history?character_id=${characterId}&limit=${limit}`),
  getConversations: () => request<{ data: Conversation[] }>('/conversations'),

  forceTick: () => request('/admin/tick', { method: 'POST' }),
  getAdminStatus: () => request<AdminStatus>('/admin/status'),

  getScenes: () => request<{ data: Scene[] }>('/town/scenes'),
  getScene: (id: string) => request<Scene>(`/town/scenes/${id}`),
};
