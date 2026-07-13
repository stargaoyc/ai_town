import { createFileRoute } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { useState, useEffect } from "react";
import {
  Settings as SettingsIcon,
  Cpu,
  Database,
  Globe,
  Bot,
  Server,
  Wrench,
  Activity,
  Boxes,
  Sliders,
  RotateCcw,
  Save,
} from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  StatusBadge,
} from "@/components/ui";
import {
  useAdminStatus,
  useModules,
  useMcpServers,
  useMcpTools,
  useMcpServersHealth,
  useToggleMcpServer,
  useHealth,
  useConfig,
  useUpdateConfig,
  useResetConfig,
} from "@/lib/queries";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0 },
};

// 将模块状态字符串映射为 StatusBadge 的状态
function mapModuleStatus(status: string): "ok" | "error" | "warning" | "idle" {
  const s = status.toLowerCase();
  if (["running", "ok", "active", "healthy", "up", "connected"].includes(s)) {
    return "ok";
  }
  if (
    ["error", "failed", "stopped", "crashed", "down", "disconnected"].includes(
      s,
    )
  ) {
    return "error";
  }
  if (["warning", "degraded", "slow", "pending"].includes(s)) {
    return "warning";
  }
  return "idle";
}

function SettingsPage() {
  // 获取系统状态、健康检查、模块列表、MCP 服务器与工具
  const {
    data: adminStatus,
    isLoading: adminLoading,
    error: adminError,
  } = useAdminStatus();
  const { data: health } = useHealth();
  const {
    data: modulesData,
    isLoading: modulesLoading,
    error: modulesError,
  } = useModules();
  const {
    data: mcpServersData,
    isLoading: serversLoading,
    error: serversError,
  } = useMcpServers();
  const {
    data: mcpToolsData,
    isLoading: toolsLoading,
    error: toolsError,
  } = useMcpTools();
  const { data: healthData } = useMcpServersHealth();
  const toggleMcpServer = useToggleMcpServer();
  const { data: configData } = useConfig();
  const updateConfig = useUpdateConfig();
  const resetConfig = useResetConfig();

  const configItems = configData?.data ?? [];
  const [editValues, setEditValues] = useState<Record<string, unknown>>({});
  const [hasEdits, setHasEdits] = useState(false);

  // 同步配置项到编辑状态
  useEffect(() => {
    if (configItems.length > 0 && !hasEdits) {
      const vals: Record<string, unknown> = {};
      configItems.forEach((item) => {
        vals[item.key] = item.current;
      });
      setEditValues(vals);
    }
  }, [configItems, hasEdits]);

  const handleSaveConfig = () => {
    // 只提交有变化的项
    const updates: Record<string, unknown> = {};
    configItems.forEach((item) => {
      if (editValues[item.key] !== item.current) {
        updates[item.key] = editValues[item.key];
      }
    });
    if (Object.keys(updates).length === 0) return;
    updateConfig.mutate(updates, {
      onSuccess: () => {
        setHasEdits(false);
      },
    });
  };

  const handleResetConfig = (key: string) => {
    resetConfig.mutate(key, {
      onSuccess: () => {
        setHasEdits(false);
      },
    });
  };

  const modules = modulesData?.data ?? [];
  const mcpServers = mcpServersData?.data ?? [];
  const mcpTools = mcpToolsData?.data ?? [];
  const healthMap = new Map((healthData?.data ?? []).map((h) => [h.name, h]));

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="系统设置"
        subtitle="查看系统配置、模块状态，控制 MCP 插件开关与运行时配置"
        icon="🔧"
      />

      {/* 提示横幅 */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center gap-2 px-4 py-2.5 rounded-2xl bg-sakura-50/80 border border-sakura-200/50 text-sm text-sakura-600"
      >
        <Sliders className="w-4 h-4 shrink-0" />
        <span>
          MCP 插件可单独开关，运行时配置可在线编辑并即时生效，无需重启服务
        </span>
      </motion.div>

      {/* 加载与错误状态 */}
      {adminLoading && <LoadingSpinner text="正在加载系统状态..." />}
      {adminError && <ErrorDisplay error={adminError} />}

      {adminStatus && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="space-y-6"
        >
          {/* 系统信息总览 */}
          <motion.div variants={item}>
            <GlassCard hover={false}>
              <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
                <SettingsIcon className="w-5 h-5" />
                系统信息
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {/* World Engine */}
                <div className="p-4 rounded-2xl bg-gradient-to-br from-white/40 to-sakura-50/30 border border-white/30">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm text-twilight-400 flex items-center gap-1.5">
                      <Globe className="w-4 h-4 text-sakura-400" />
                      World Engine
                    </span>
                  </div>
                  <StatusBadge
                    status={adminStatus.world_engine.running ? "ok" : "error"}
                    label={
                      adminStatus.world_engine.running ? "运行中" : "已停止"
                    }
                  />
                  <div className="mt-2 text-xs text-twilight-400 space-y-0.5">
                    <div>Tick ID: #{adminStatus.world_engine.tick_id}</div>
                    <div>
                      Leader: {adminStatus.world_engine.is_leader ? "是" : "否"}
                    </div>
                  </div>
                </div>

                {/* Character Engine */}
                <div className="p-4 rounded-2xl bg-gradient-to-br from-white/40 to-sky-soft-50/30 border border-white/30">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm text-twilight-400 flex items-center gap-1.5">
                      <Bot className="w-4 h-4 text-sky-soft-400" />
                      Character Engine
                    </span>
                  </div>
                  <StatusBadge
                    status={
                      adminStatus.character_engine.available ? "ok" : "idle"
                    }
                    label={
                      adminStatus.character_engine.available ? "可用" : "未启动"
                    }
                  />
                  <div className="mt-2 text-xs text-twilight-400">
                    Tick 间隔: {adminStatus.character_engine.tick_interval}s
                  </div>
                </div>

                {/* LLM 模型 */}
                <div className="p-4 rounded-2xl bg-gradient-to-br from-white/40 to-twilight-50/30 border border-white/30">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm text-twilight-400 flex items-center gap-1.5">
                      <Cpu className="w-4 h-4 text-twilight-400" />
                      LLM 模型
                    </span>
                  </div>
                  <StatusBadge
                    status={adminStatus.llm.initialized ? "ok" : "error"}
                    label={
                      adminStatus.llm.initialized ? "已初始化" : "未初始化"
                    }
                  />
                  <div className="mt-2 text-xs text-twilight-400 font-mono truncate">
                    {adminStatus.llm.model || "—"}
                  </div>
                </div>

                {/* Redis */}
                <div className="p-4 rounded-2xl bg-gradient-to-br from-white/40 to-emerald-50/30 border border-white/30">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm text-twilight-400 flex items-center gap-1.5">
                      <Database className="w-4 h-4 text-emerald-400" />
                      Redis
                    </span>
                  </div>
                  <StatusBadge
                    status={adminStatus.redis === "connected" ? "ok" : "error"}
                    label={
                      adminStatus.redis === "connected" ? "已连接" : "断开"
                    }
                  />
                  <div className="mt-2 text-xs text-twilight-400">
                    {adminStatus.redis || "—"}
                  </div>
                </div>
              </div>

              {/* Action Registry 信息 */}
              <div className="mt-4 p-3 rounded-xl bg-white/40 border border-white/30 flex items-center gap-3 flex-wrap">
                <Activity className="w-4 h-4 text-sakura-400" />
                <span className="text-sm text-twilight-500 font-medium">
                  Action Registry
                </span>
                <StatusBadge
                  status={
                    adminStatus.action_registry.initialized ? "ok" : "idle"
                  }
                  label={
                    adminStatus.action_registry.initialized
                      ? "已初始化"
                      : "未初始化"
                  }
                />
                <span className="text-xs text-twilight-400">
                  已注册行为数: {adminStatus.action_registry.action_count}
                </span>
              </div>
            </GlassCard>
          </motion.div>

          {/* 健康检查信息（只读） */}
          <motion.div variants={item}>
            <GlassCard hover={false}>
              <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
                <Activity className="w-5 h-5" />
                健康检查 /health
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="p-3 rounded-xl bg-white/40 border border-white/30">
                  <div className="text-xs text-twilight-400 mb-1">服务状态</div>
                  <StatusBadge
                    status={health?.status === "ok" ? "ok" : "warning"}
                    label={health?.status ?? "未知"}
                  />
                </div>
                <div className="p-3 rounded-xl bg-white/40 border border-white/30">
                  <div className="text-xs text-twilight-400 mb-1">
                    World Tick
                  </div>
                  <div className="text-lg font-bold text-twilight-500">
                    #{health?.world_tick ?? "—"}
                  </div>
                </div>
                <div className="p-3 rounded-xl bg-white/40 border border-white/30">
                  <div className="text-xs text-twilight-400 mb-1">Redis</div>
                  <StatusBadge
                    status={health?.redis === "connected" ? "ok" : "error"}
                    label={health?.redis ?? "未知"}
                  />
                </div>
              </div>
            </GlassCard>
          </motion.div>

          {/* LLM 配置展示 */}
          <motion.div variants={item}>
            <GlassCard hover={false}>
              <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
                <Cpu className="w-5 h-5" />
                LLM 配置
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="p-3 rounded-xl bg-white/40 border border-white/30">
                  <div className="text-xs text-twilight-400 mb-1">模型名称</div>
                  <div className="text-sm font-mono text-twilight-600 break-all">
                    {adminStatus.llm.model || "未配置"}
                  </div>
                </div>
                <div className="p-3 rounded-xl bg-white/40 border border-white/30">
                  <div className="text-xs text-twilight-400 mb-1">
                    初始化状态
                  </div>
                  <StatusBadge
                    status={adminStatus.llm.initialized ? "ok" : "error"}
                    label={adminStatus.llm.initialized ? "已就绪" : "未就绪"}
                  />
                </div>
              </div>
            </GlassCard>
          </motion.div>
        </motion.div>
      )}

      {/* 模块列表 */}
      <motion.div variants={container} initial="hidden" animate="show">
        <motion.div variants={item}>
          <GlassCard hover={false}>
            <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
              <Boxes className="w-5 h-5" />
              模块列表
            </h3>
            {modulesError && <ErrorDisplay error={modulesError} />}
            {modulesLoading && !modulesError && (
              <LoadingSpinner text="正在加载模块列表..." />
            )}
            {!modulesLoading && !modulesError && modules.length === 0 && (
              <EmptyState
                icon="📦"
                title="暂无模块"
                subtitle="系统暂未注册任何模块"
              />
            )}
            {modules.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {modules.map((mod, idx) => (
                  <div
                    key={`${mod.name}-${idx}`}
                    className="p-3 rounded-xl bg-white/40 border border-white/30 flex items-start gap-3"
                  >
                    <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-sakura-100 to-twilight-100 flex items-center justify-center shrink-0">
                      <Boxes className="w-4 h-4 text-sakura-500" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-twilight-600 text-sm">
                          {mod.name}
                        </span>
                        <span className="text-xs text-twilight-300 px-1.5 py-0.5 rounded bg-white/50">
                          {mod.type}
                        </span>
                      </div>
                      <p className="text-xs text-twilight-400 mt-1 break-words">
                        {mod.description || "无描述"}
                      </p>
                    </div>
                    <StatusBadge
                      status={mapModuleStatus(mod.status)}
                      label={mod.status}
                    />
                  </div>
                ))}
              </div>
            )}
          </GlassCard>
        </motion.div>
      </motion.div>

      {/* 顶部统计 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          title="模块总数"
          value={modules.length}
          icon="📦"
          color="sakura"
        />
        <StatCard
          title="MCP 服务器"
          value={mcpServers.length}
          icon="🖥️"
          color="sky"
        />
        <StatCard
          title="MCP 工具"
          value={mcpTools.length}
          icon="🔧"
          color="twilight"
        />
        <StatCard
          title="运行中模块"
          value={
            modules.filter((m) => mapModuleStatus(m.status) === "ok").length
          }
          icon="✅"
          color="sakura"
        />
      </div>

      {/* MCP 服务器列表 */}
      <motion.div variants={container} initial="hidden" animate="show">
        <motion.div variants={item}>
          <GlassCard hover={false}>
            <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
              <h3 className="font-semibold text-sakura-600 flex items-center gap-2 text-lg">
                <Server className="w-5 h-5" />
                MCP 服务器
              </h3>
              {healthData && (
                <div className="flex items-center gap-3 text-xs">
                  <span className="flex items-center gap-1 text-emerald-600">
                    <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    在线 {healthData.online}
                  </span>
                  <span className="flex items-center gap-1 text-red-500">
                    <span className="w-2 h-2 rounded-full bg-red-500" />
                    离线 {healthData.offline}
                  </span>
                </div>
              )}
            </div>
            {serversError && <ErrorDisplay error={serversError} />}
            {serversLoading && !serversError && (
              <LoadingSpinner text="正在加载 MCP 服务器..." />
            )}
            {!serversLoading && !serversError && mcpServers.length === 0 && (
              <EmptyState
                icon="🖥️"
                title="暂无 MCP 服务器"
                subtitle="未配置任何 MCP 服务器"
              />
            )}
            {mcpServers.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {mcpServers.map((srv, idx) => {
                  const health = healthMap.get(srv.name);
                  const isOnline = health?.status === "online";
                  const isEnabled = srv.enabled !== false;
                  return (
                    <div
                      key={`${srv.name}-${idx}`}
                      className={`p-3 rounded-xl border flex items-start gap-3 transition-all ${
                        isEnabled
                          ? "bg-white/40 border-white/30"
                          : "bg-gray-50/40 border-gray-200/30 opacity-70"
                      }`}
                    >
                      <div
                        className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${isOnline && isEnabled ? "bg-gradient-to-br from-emerald-100 to-sky-soft-100" : "bg-gradient-to-br from-gray-100 to-gray-200"}`}
                      >
                        <Server
                          className={`w-4 h-4 ${isOnline && isEnabled ? "text-emerald-500" : "text-gray-400"}`}
                        />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-twilight-600 text-sm">
                            {srv.name}
                          </span>
                          <span className="text-xs text-twilight-300 px-1.5 py-0.5 rounded bg-white/50">
                            {srv.type}
                          </span>
                          <StatusBadge
                            status={
                              isOnline && isEnabled
                                ? "ok"
                                : isEnabled
                                  ? "warning"
                                  : "idle"
                            }
                            label={
                              !isEnabled
                                ? "已禁用"
                                : isOnline
                                  ? `在线 ${health?.latency_ms ?? 0}ms`
                                  : "离线"
                            }
                          />
                        </div>
                        <p className="text-xs text-twilight-400 mt-1 break-words">
                          {srv.description || "无描述"}
                        </p>
                        <p className="text-xs text-twilight-300 mt-1 font-mono truncate">
                          {health?.endpoint ?? "—"}
                        </p>
                      </div>
                      {/* 启用/禁用开关 */}
                      <button
                        onClick={() =>
                          toggleMcpServer.mutate({
                            serverName: srv.name,
                            enabled: !isEnabled,
                          })
                        }
                        disabled={toggleMcpServer.isPending}
                        title={
                          isEnabled
                            ? "点击禁用此 MCP 插件"
                            : "点击启用此 MCP 插件"
                        }
                        className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
                          isEnabled ? "bg-sakura-400" : "bg-gray-300"
                        } ${toggleMcpServer.isPending ? "opacity-50 cursor-wait" : "cursor-pointer"}`}
                      >
                        <span
                          className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow-sm transition-transform ${
                            isEnabled ? "translate-x-5" : ""
                          }`}
                        />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </GlassCard>
        </motion.div>
      </motion.div>

      {/* MCP 工具列表 */}
      <motion.div variants={container} initial="hidden" animate="show">
        <motion.div variants={item}>
          <GlassCard hover={false}>
            <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
              <Wrench className="w-5 h-5" />
              MCP 工具
            </h3>
            {toolsError && <ErrorDisplay error={toolsError} />}
            {toolsLoading && !toolsError && (
              <LoadingSpinner text="正在加载 MCP 工具..." />
            )}
            {!toolsLoading && !toolsError && mcpTools.length === 0 && (
              <EmptyState
                icon="🔧"
                title="暂无 MCP 工具"
                subtitle="未注册任何 MCP 工具"
              />
            )}
            {mcpTools.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {mcpTools.map((tool, idx) => (
                  <div
                    key={`${tool.name}-${idx}`}
                    className="p-3 rounded-xl bg-white/40 border border-white/30 flex items-start gap-3"
                  >
                    <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-twilight-100 to-sakura-100 flex items-center justify-center shrink-0">
                      <Wrench className="w-3.5 h-3.5 text-twilight-500" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-twilight-600 text-sm truncate">
                        {tool.name}
                      </div>
                      <div className="text-xs text-twilight-400 mt-0.5 truncate">
                        {tool.server}
                        {tool.server_type && (
                          <span className="ml-1 text-twilight-300">
                            · {tool.server_type}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </GlassCard>
        </motion.div>
      </motion.div>

      {/* 运行时配置编辑器 */}
      <motion.div variants={container} initial="hidden" animate="show">
        <motion.div variants={item}>
          <GlassCard hover={false}>
            <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
              <h3 className="font-semibold text-sakura-600 flex items-center gap-2 text-lg">
                <Sliders className="w-5 h-5" />
                运行时配置
              </h3>
              <div className="flex items-center gap-2">
                {hasEdits && (
                  <span className="text-xs text-amber-600 flex items-center gap-1">
                    <Activity className="w-3 h-3" />
                    有未保存的修改
                  </span>
                )}
                <button
                  onClick={handleSaveConfig}
                  disabled={!hasEdits || updateConfig.isPending}
                  className={`px-4 py-1.5 rounded-xl text-xs font-medium flex items-center gap-1.5 transition-all ${
                    hasEdits && !updateConfig.isPending
                      ? "bg-sakura-100 text-sakura-600 hover:bg-sakura-200 shadow-sm"
                      : "bg-gray-100 text-gray-400 cursor-not-allowed"
                  }`}
                >
                  <Save className="w-3.5 h-3.5" />
                  {updateConfig.isPending ? "保存中..." : "保存配置"}
                </button>
              </div>
            </div>
            <p className="text-xs text-twilight-400 mb-4">
              修改后点击保存即可生效，无需重启服务。环境变量值为默认值，Redis
              覆盖值为当前运行值。
            </p>
            {configItems.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {configItems.map((item) => {
                  const isOverridden = item.overridden;
                  const isChanged = editValues[item.key] !== item.current;
                  return (
                    <div
                      key={item.key}
                      className={`p-3 rounded-xl border transition-all ${
                        isChanged
                          ? "bg-amber-50/60 border-amber-200/50"
                          : "bg-white/40 border-white/30"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-twilight-600">
                            {item.label}
                          </span>
                          {isOverridden && (
                            <span className="text-xs text-sakura-500 px-1.5 py-0.5 rounded bg-sakura-50">
                              已覆盖
                            </span>
                          )}
                        </div>
                        {isOverridden && (
                          <button
                            onClick={() => handleResetConfig(item.key)}
                            disabled={resetConfig.isPending}
                            className="text-xs text-twilight-400 hover:text-sakura-500 flex items-center gap-1 transition-colors"
                            title="重置为默认值"
                          >
                            <RotateCcw className="w-3 h-3" />
                            重置
                          </button>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {item.type === "bool" ? (
                          <button
                            onClick={() => {
                              setEditValues((prev) => ({
                                ...prev,
                                [item.key]: !prev[item.key],
                              }));
                              setHasEdits(true);
                            }}
                            className={`relative w-12 h-6 rounded-full transition-colors ${
                              editValues[item.key]
                                ? "bg-sakura-400"
                                : "bg-gray-300"
                            }`}
                          >
                            <span
                              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow-sm transition-transform ${
                                editValues[item.key] ? "translate-x-6" : ""
                              }`}
                            />
                          </button>
                        ) : item.type === "float" || item.type === "int" ? (
                          <input
                            type="number"
                            step={item.type === "float" ? "0.01" : "1"}
                            value={String(editValues[item.key] ?? "")}
                            onChange={(e) => {
                              const val =
                                item.type === "float"
                                  ? parseFloat(e.target.value)
                                  : parseInt(e.target.value, 10);
                              setEditValues((prev) => ({
                                ...prev,
                                [item.key]: isNaN(val) ? 0 : val,
                              }));
                              setHasEdits(true);
                            }}
                            className="flex-1 px-3 py-1.5 rounded-lg bg-white/60 border border-sakura-200/40 text-twilight-600 text-sm focus:outline-none focus:ring-2 focus:ring-sakura-400/30"
                          />
                        ) : (
                          <input
                            type="text"
                            value={String(editValues[item.key] ?? "")}
                            onChange={(e) => {
                              setEditValues((prev) => ({
                                ...prev,
                                [item.key]: e.target.value,
                              }));
                              setHasEdits(true);
                            }}
                            className="flex-1 px-3 py-1.5 rounded-lg bg-white/60 border border-sakura-200/40 text-twilight-600 text-sm focus:outline-none focus:ring-2 focus:ring-sakura-400/30"
                          />
                        )}
                        <span className="text-xs text-twilight-300 font-mono shrink-0">
                          默认: {String(item.default)}
                        </span>
                      </div>
                      <div className="text-xs text-twilight-300 mt-1 font-mono">
                        {item.key}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {configItems.length === 0 && (
              <LoadingSpinner text="正在加载配置..." />
            )}
            {updateConfig.isError && (
              <ErrorDisplay error={updateConfig.error as Error} />
            )}
          </GlassCard>
        </motion.div>
      </motion.div>
    </div>
  );
}
