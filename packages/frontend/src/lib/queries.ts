import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

// queryKeys 是 TanStack Query 缓存失效的契约：所有 queryKey / invalidateQueries
// 必须从这里取值，禁止内联字面量数组。key 值与迁移前完全一致（含 undefined 占位），
// 前缀匹配语义依赖数组元素顺序，不得增删元素。
//
// *All / *ByCharacter 后缀是「前缀失效锚点」：比参数化 key 少一层元素，用于
// 失效该域的全部变体。不能用参数化 key 传 undefined 代替——
// ["characters", undefined] 无法前缀匹配 ["characters", { active_only: true }]。
export const queryKeys = {
  // ===== 全局 =====
  health: ["health"] as const,
  world: ["world"] as const,
  scenes: ["scenes"] as const,
  adminStatus: ["adminStatus"] as const,
  config: ["config"] as const,
  modules: ["modules"] as const,
  detailedMetrics: ["detailedMetrics"] as const,

  // ===== 角色域 =====
  characters: (params?: { active_only?: boolean }) => ["characters", params] as const,
  charactersAll: ["characters"] as const,
  character: (id: string) => ["character", id] as const,
  memories: (id: string) => ["memories", id] as const,
  messages: (characterId: string) => ["messages", characterId] as const,
  exportHistory: (characterId: string) => ["exportHistory", characterId] as const,
  actions: ["actions"] as const,
  characterActions: (characterId: string, limit: number) =>
    ["characterActions", characterId, limit] as const,
  stateHistory: (characterId: string, limit: number) =>
    ["stateHistory", characterId, limit] as const,
  nearbyCharacters: (characterId: string) => ["nearbyCharacters", characterId] as const,
  relations: (characterId: string) => ["relations", characterId] as const,
  reflections: (characterId: string) => ["reflections", characterId] as const,
  plans: (characterId: string) => ["plans", characterId] as const,

  // ===== 日记与角色对用户的记忆 =====
  diaries: (characterId: string, params?: { period?: string; limit?: number }) =>
    ["diaries", characterId, params] as const,
  diariesByCharacter: (characterId: string) => ["diaries", characterId] as const,
  personMemory: (characterId: string, userId: string) =>
    ["personMemory", characterId, userId] as const,
  personMemoriesList: (characterId: string, limit: number) =>
    ["personMemoriesList", characterId, limit] as const,

  // ===== 会话与消息统计 =====
  conversations: ["conversations"] as const,
  messageStats: (params?: { character_id?: string; start_date?: string; end_date?: string }) =>
    ["messageStats", params] as const,

  // ===== 世界 =====
  worldEvents: (params: {
    start_tick?: number;
    end_tick?: number;
    event_type?: string;
    limit?: number;
  }) => ["worldEvents", params] as const,
  worldSnapshots: (limit: number) => ["worldSnapshots", limit] as const,

  // ===== 运维与平台集成 =====
  logs: (lines: number, level?: string) => ["logs", lines, level] as const,
  onebotMessages: (limit: number) => ["onebotMessages", limit] as const,
  proactiveShares: (limit: number) => ["proactiveShares", limit] as const,
  mcpServers: ["mcpServers"] as const,
  mcpTools: ["mcpTools"] as const,
  mcpServersHealth: ["mcpServersHealth"] as const,

  // ===== 通知 =====
  notifications: (limit: number) => ["notifications", limit] as const,
  notificationsAll: ["notifications"] as const,
};

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: api.getHealth,
    refetchInterval: 30000, // /ws/dashboard 推送会主动 invalidate，此为断连兜底
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
    refetchInterval: 30000, // /ws/dashboard 推送会主动 invalidate，此为断连兜底
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
    // 运维状态页非关键路径，30s 新鲜度可接受；无 WS 推送覆盖此域
    refetchInterval: 30000,
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
    queryKey: queryKeys.reflections(characterId),
    queryFn: () => api.getReflections(characterId),
    enabled: !!characterId,
  });
}

export function usePlans(characterId: string) {
  return useQuery({
    queryKey: queryKeys.plans(characterId),
    queryFn: () => api.getPlans(characterId),
    enabled: !!characterId,
  });
}

export function useCharacterActions(characterId: string, limit = 50) {
  return useQuery({
    queryKey: queryKeys.characterActions(characterId, limit),
    queryFn: () => api.getCharacterActions(characterId, limit),
    enabled: !!characterId,
  });
}

export function useRelations(characterId: string) {
  return useQuery({
    queryKey: queryKeys.relations(characterId),
    queryFn: () => api.getRelations(characterId),
    enabled: !!characterId,
  });
}

export function useNearbyCharacters(characterId: string) {
  return useQuery({
    queryKey: queryKeys.nearbyCharacters(characterId),
    queryFn: () => api.getNearbyCharacters(characterId),
    enabled: !!characterId,
    // 场景位置变化由 Tick 驱动（秒级周期），30s 轮询足够感知；10s 过于激进
    refetchInterval: 30000,
  });
}

export function useStateHistory(characterId: string, limit = 50) {
  return useQuery({
    queryKey: queryKeys.stateHistory(characterId, limit),
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
    queryKey: queryKeys.worldEvents(params),
    queryFn: () => api.getWorldEventsRange(params),
  });
}

export function useOnebotMessages(limit = 50) {
  return useQuery({
    queryKey: queryKeys.onebotMessages(limit),
    queryFn: () => api.getOnebotMessages(limit),
    // 监控页消息流非即时操作路径，30s 新鲜度可接受；qq-monitor 文案同步此节奏
    refetchInterval: 30000,
  });
}

export function useProactiveShares(limit = 50) {
  return useQuery({
    queryKey: queryKeys.proactiveShares(limit),
    queryFn: () => api.getProactiveShares(limit),
  });
}

export function useWorldSnapshots(limit = 20) {
  return useQuery({
    queryKey: queryKeys.worldSnapshots(limit),
    queryFn: () => api.getWorldSnapshots(limit),
  });
}

export function useMessageStats(params?: {
  character_id?: string;
  start_date?: string;
  end_date?: string;
}) {
  return useQuery({
    queryKey: queryKeys.messageStats(params),
    queryFn: () => api.getMessageStats(params),
  });
}

export function useModules() {
  return useQuery({
    queryKey: queryKeys.modules,
    queryFn: () => api.getModules(),
  });
}

export function useMcpServers() {
  return useQuery({
    queryKey: queryKeys.mcpServers,
    queryFn: () => api.getMcpServers(),
  });
}

export function useMcpTools() {
  return useQuery({
    queryKey: queryKeys.mcpTools,
    queryFn: () => api.getMcpTools(),
  });
}

export function useMcpServersHealth(refetchInterval = 10000) {
  return useQuery({
    queryKey: queryKeys.mcpServersHealth,
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
    mutationFn: ({ serverName, enabled }: { serverName: string; enabled: boolean }) =>
      api.toggleMcpServer(serverName, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.mcpServers });
      qc.invalidateQueries({ queryKey: queryKeys.mcpTools });
    },
  });
}

export function useImportCharacter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (yaml: string) => api.importCharacter(yaml),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.charactersAll });
    },
  });
}

export function useImportCharacterBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (yaml: string) => api.importCharacterBatch(yaml),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.charactersAll });
    },
  });
}

export function useDeleteCharacter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (characterId: string) => api.deleteCharacter(characterId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.charactersAll });
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
      characterId: string | null;
      query: string;
      topK?: number;
    }) => api.vectorSearch(characterId, query, topK),
  });
}

export function useLogs(lines = 100, level?: string, refetchInterval = 15000) {
  return useQuery({
    queryKey: queryKeys.logs(lines, level),
    queryFn: () => api.getLogs(lines, level),
    // 日志页承担 live-tail 观感，保持比其他 ops 页更紧的节奏，但不再 5s 打后端
    refetchInterval,
  });
}

export function useDetailedMetrics(refetchInterval = 15000) {
  return useQuery({
    queryKey: queryKeys.detailedMetrics,
    queryFn: () => api.getDetailedMetrics(),
    // 指标曲线粒度 15s 足够；Prometheus 抓取周期本身为秒级聚合
    refetchInterval,
  });
}

export function useConfig() {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: () => api.getConfig(),
  });
}

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (updates: Record<string, unknown>) => api.updateConfig(updates),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.config });
    },
  });
}

export function useResetConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.resetConfig(key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.config });
    },
  });
}

// ===== 通知中心 =====

export function useNotifications(limit = 50) {
  return useQuery({
    queryKey: queryKeys.notifications(limit),
    queryFn: () => api.getNotifications(limit),
    // 不设 refetchInterval：未读数变化由 /ws/dashboard 推送触发
    // useDashboardSocket invalidate（见 hooks/useDashboardSocket.ts），轮询纯冗余
  });
}

export function useCreateNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ type, title, content }: { type: string; title: string; content: string }) =>
      api.createNotification(type, title, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.notificationsAll });
    },
  });
}

export function useMarkNotificationRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.markNotificationRead(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.notificationsAll });
    },
  });
}

export function useMarkAllNotificationsRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.markAllNotificationsRead(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.notificationsAll });
    },
  });
}

export function useDeleteNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteNotification(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.notificationsAll });
    },
  });
}

export function useClearAllNotifications() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.clearAllNotifications(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.notificationsAll });
    },
  });
}

// ===== 日记系统 =====

export function useDiaries(characterId: string, params?: { period?: string; limit?: number }) {
  return useQuery({
    queryKey: queryKeys.diaries(characterId, params),
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
      qc.invalidateQueries({ queryKey: queryKeys.diariesByCharacter(vars.characterId) });
    },
  });
}

// ===== 角色对用户的记忆 =====

export function usePersonMemory(characterId: string, userId: string) {
  return useQuery({
    queryKey: queryKeys.personMemory(characterId, userId),
    queryFn: () => api.getPersonMemory(characterId, userId),
    enabled: !!characterId && !!userId,
  });
}

export function usePersonMemoriesList(characterId: string, limit = 50) {
  return useQuery({
    queryKey: queryKeys.personMemoriesList(characterId, limit),
    queryFn: () => api.listPersonMemories(characterId, limit),
    enabled: !!characterId,
  });
}
