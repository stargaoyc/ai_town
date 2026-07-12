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

// ===== 扩展查询钩子 =====

export function useReflections(characterId: string) {
  return useQuery({
    queryKey: ["reflections", characterId],
    queryFn: () => api.getReflections(characterId),
    enabled: !!characterId,
  });
}

export function usePlans(characterId: string) {
  return useQuery({
    queryKey: ["plans", characterId],
    queryFn: () => api.getPlans(characterId),
    enabled: !!characterId,
  });
}

export function useCharacterActions(characterId: string, limit = 50) {
  return useQuery({
    queryKey: ["characterActions", characterId, limit],
    queryFn: () => api.getCharacterActions(characterId, limit),
    enabled: !!characterId,
  });
}

export function useRelations(characterId: string) {
  return useQuery({
    queryKey: ["relations", characterId],
    queryFn: () => api.getRelations(characterId),
    enabled: !!characterId,
  });
}

export function useStateHistory(characterId: string, limit = 50) {
  return useQuery({
    queryKey: ["stateHistory", characterId, limit],
    queryFn: () => api.getCharacterStateHistory(characterId, limit),
    enabled: !!characterId,
  });
}

export function useWorldEventsRange(params: {
  start_tick?: number;
  end_tick?: number;
  event_type?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["worldEvents", params],
    queryFn: () => api.getWorldEventsRange(params),
  });
}

export function useOnebotMessages(limit = 50) {
  return useQuery({
    queryKey: ["onebotMessages", limit],
    queryFn: () => api.getOnebotMessages(limit),
    refetchInterval: 10000,
  });
}

export function useProactiveShares(limit = 50) {
  return useQuery({
    queryKey: ["proactiveShares", limit],
    queryFn: () => api.getProactiveShares(limit),
  });
}

export function useWorldSnapshots(limit = 20) {
  return useQuery({
    queryKey: ["worldSnapshots", limit],
    queryFn: () => api.getWorldSnapshots(limit),
  });
}

export function useMessageStats(params?: {
  character_id?: string;
  start_date?: string;
  end_date?: string;
}) {
  return useQuery({
    queryKey: ["messageStats", params],
    queryFn: () => api.getMessageStats(params),
  });
}

export function useModules() {
  return useQuery({
    queryKey: ["modules"],
    queryFn: () => api.getModules(),
  });
}

export function useMcpServers() {
  return useQuery({
    queryKey: ["mcpServers"],
    queryFn: () => api.getMcpServers(),
  });
}

export function useMcpTools() {
  return useQuery({
    queryKey: ["mcpTools"],
    queryFn: () => api.getMcpTools(),
  });
}

export function useImportCharacter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (yaml: string) => api.importCharacter(yaml),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["characters"] });
    },
  });
}

export function useImportCharacterBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (yaml: string) => api.importCharacterBatch(yaml),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["characters"] });
    },
  });
}

export function useVectorSearch() {
  return useMutation({
    mutationFn: ({
      characterId,
      query,
      topK,
    }: {
      characterId: string;
      query: string;
      topK?: number;
    }) => api.vectorSearch(characterId, query, topK),
  });
}
