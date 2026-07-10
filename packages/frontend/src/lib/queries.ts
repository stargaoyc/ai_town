import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export const queryKeys = {
  health: ["health"] as const,
  characters: (params?: { active_only?: boolean }) =>
    ["characters", params] as const,
  character: (id: string) => ["character", id] as const,
  world: ["world"] as const,
  actions: ["actions"] as const,
  memories: (id: string) => ["memories", id] as const,
  conversations: ["conversations"] as const,
  messages: (characterId: string) => ["messages", characterId] as const,
  scenes: ["scenes"] as const,
  adminStatus: ["adminStatus"] as const,
};

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: api.getHealth,
    refetchInterval: 5000,
  });
}
export function useCharacters(params?: { active_only?: boolean }) {
  return useQuery({
    queryKey: queryKeys.characters(params),
    queryFn: () => api.getCharacters(params),
  });
}
export function useCharacter(id: string) {
  return useQuery({
    queryKey: queryKeys.character(id),
    queryFn: () => api.getCharacter(id),
    enabled: !!id,
  });
}
export function useWorld() {
  return useQuery({
    queryKey: queryKeys.world,
    queryFn: api.getWorld,
    refetchInterval: 5000,
  });
}
export function useActions() {
  return useQuery({ queryKey: queryKeys.actions, queryFn: api.getActions });
}
export function useMemories(characterId: string, limit = 20) {
  return useQuery({
    queryKey: queryKeys.memories(characterId),
    queryFn: () => api.getMemories(characterId, limit),
    enabled: !!characterId,
  });
}
export function useMessages(characterId: string, limit = 20) {
  return useQuery({
    queryKey: queryKeys.messages(characterId),
    queryFn: () => api.getHistory(characterId, limit),
    enabled: !!characterId,
  });
}
export function useScenes() {
  return useQuery({ queryKey: queryKeys.scenes, queryFn: api.getScenes });
}
export function useAdminStatus() {
  return useQuery({
    queryKey: queryKeys.adminStatus,
    queryFn: api.getAdminStatus,
    refetchInterval: 10000,
  });
}

export function useSendMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      characterId,
      userId,
      content,
    }: {
      characterId: string;
      userId: string;
      content: string;
    }) => api.sendMessage(characterId, userId, content),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: queryKeys.messages(vars.characterId) });
      qc.invalidateQueries({ queryKey: queryKeys.conversations });
    },
  });
}

export function useForceTick() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.forceTick,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.world });
      qc.invalidateQueries({ queryKey: queryKeys.adminStatus });
    },
  });
}
