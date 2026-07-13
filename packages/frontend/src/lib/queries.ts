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

export function useMcpServersHealth(refetchInterval = 10000) {
  return useQuery({
    queryKey: ["mcpServersHealth"],
    queryFn: () => api.getMcpServersHealth(),
    refetchInterval,
  });
}

export function useInvokeMcpTool() {
  return useMutation({
    mutationFn: ({
      toolName,
      serverName,
      args,
    }: {
      toolName: string;
      serverName: string;
      args: Record<string, unknown>;
    }) => api.invokeMcpTool(toolName, serverName, args),
  });
}

export function useToggleMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      serverName,
      enabled,
    }: {
      serverName: string;
      enabled: boolean;
    }) => api.toggleMcpServer(serverName, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcpServers"] });
      qc.invalidateQueries({ queryKey: ["mcpTools"] });
    },
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

export function useLogs(lines = 100, level?: string, refetchInterval = 5000) {
  return useQuery({
    queryKey: ["logs", lines, level],
    queryFn: () => api.getLogs(lines, level),
    refetchInterval,
  });
}

export function useDetailedMetrics(refetchInterval = 5000) {
  return useQuery({
    queryKey: ["detailedMetrics"],
    queryFn: () => api.getDetailedMetrics(),
    refetchInterval,
  });
}

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => api.getConfig(),
  });
}

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (updates: Record<string, unknown>) => api.updateConfig(updates),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}

export function useResetConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.resetConfig(key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}

// ===== 通知中心 =====

export function useNotifications(limit = 50, refetchInterval = 10000) {
  return useQuery({
    queryKey: ["notifications", limit],
    queryFn: () => api.getNotifications(limit),
    refetchInterval,
  });
}

export function useCreateNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      type,
      title,
      content,
    }: {
      type: string;
      title: string;
      content: string;
    }) => api.createNotification(type, title, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useMarkNotificationRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.markNotificationRead(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useMarkAllNotificationsRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.markAllNotificationsRead(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useDeleteNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteNotification(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useClearAllNotifications() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.clearAllNotifications(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

// ===== 日记系统 =====

export function useDiaries(
  characterId: string,
  params?: { period?: string; limit?: number },
) {
  return useQuery({
    queryKey: ["diaries", characterId, params],
    queryFn: () => api.getDiaries(characterId, params),
    enabled: !!characterId,
  });
}

export function useGenerateDiary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      characterId,
      period,
      characterName,
    }: {
      characterId: string;
      period: string;
      characterName?: string;
    }) => api.generateDiary(characterId, period, characterName),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["diaries", vars.characterId] });
    },
  });
}

// ===== 角色对用户的记忆 =====

export function usePersonMemory(characterId: string, userId: string) {
  return useQuery({
    queryKey: ["personMemory", characterId, userId],
    queryFn: () => api.getPersonMemory(characterId, userId),
    enabled: !!characterId && !!userId,
  });
}

export function usePersonMemoriesList(characterId: string, limit = 50) {
  return useQuery({
    queryKey: ["personMemoriesList", characterId, limit],
    queryFn: () => api.listPersonMemories(characterId, limit),
    enabled: !!characterId,
  });
}
