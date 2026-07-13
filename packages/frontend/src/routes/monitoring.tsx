import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import {
  Activity,
  Database,
  Cpu,
  Clock,
  Server,
  Zap,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Terminal,
  RefreshCw,
} from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  StatusBadge,
} from "@/components/ui";
import { useDetailedMetrics, useLogs } from "@/lib/queries";

export const Route = createFileRoute("/monitoring")({
  component: MonitoringPage,
});

// 格式化数字
function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
  return n.toLocaleString();
}

// 格式化时间
function formatLogTime(ts?: string): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}

// 日志级别颜色
const levelColors: Record<string, { bg: string; text: string; icon: string }> =
  {
    debug: { bg: "bg-gray-100", text: "text-gray-500", icon: "🔍" },
    info: { bg: "bg-sky-soft-100", text: "text-sky-soft-600", icon: "ℹ️" },
    warning: { bg: "bg-amber-100", text: "text-amber-600", icon: "⚠️" },
    warn: { bg: "bg-amber-100", text: "text-amber-600", icon: "⚠️" },
    error: { bg: "bg-red-100", text: "text-red-600", icon: "❌" },
    critical: { bg: "bg-red-200", text: "text-red-700", icon: "🔥" },
  };

// 饼图颜色（樱花粉/天空蓝/暮光紫/绿/黄）
const PIE_COLORS = ["#FF8FAB", "#7EC8E3", "#B19CD9", "#6FCF97", "#F2C94C"];

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.05 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

function MonitoringPage() {
  const [logLevel, setLogLevel] = useState<string | undefined>(undefined);
  const [logLines, setLogLines] = useState(100);

  const {
    data: metricsData,
    isLoading: metricsLoading,
    error: metricsError,
  } = useDetailedMetrics(5000);
  const {
    data: logsData,
    isLoading: logsLoading,
    error: logsError,
  } = useLogs(logLines, logLevel, 5000);

  const metrics = metricsData?.data;
  const logs = logsData?.data ?? [];

  // Action 执行统计图表数据
  const actionChartData = useMemo(() => {
    if (!metrics?.actions?.by_action) return [];
    return Object.entries(metrics.actions.by_action)
      .map(([actionId, counts]) => ({
        name: actionId,
        成功: counts.success,
        失败: counts.failed,
      }))
      .sort((a, b) => b.成功 + b.失败 - (a.成功 + a.失败))
      .slice(0, 10);
  }, [metrics]);

  // LLM Token 饼图数据
  const tokenPieData = useMemo(() => {
    if (!metrics?.llm?.tokens) return [];
    return Object.entries(metrics.llm.tokens).map(([model, tokens]) => ({
      name: model,
      value: tokens.prompt + tokens.completion,
    }));
  }, [metrics]);

  // 角色 Tick Top 5
  const charTickData = useMemo(() => {
    if (!metrics?.characters?.by_character) return [];
    return Object.entries(metrics.characters.by_character)
      .map(([charId, count]) => ({
        name: charId.slice(0, 8),
        ticks: count,
      }))
      .sort((a, b) => b.ticks - a.ticks)
      .slice(0, 5);
  }, [metrics]);

  // HTTP 请求 Top 5
  const httpReqData = useMemo(() => {
    if (!metrics?.http?.requests) return [];
    return Object.entries(metrics.http.requests)
      .map(([path, info]) => ({ name: path, 请求数: info.total }))
      .sort((a, b) => b.请求数 - a.请求数)
      .slice(0, 5);
  }, [metrics]);

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="系统监控"
        subtitle="集成 Prometheus 指标与系统日志（每 5 秒自动刷新）"
        icon="📡"
        backTo="/admin"
        backLabel="返回管理"
      />

      {/* 状态总览条 */}
      <GlassCard hover={false}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2 text-twilight-500">
            <Activity className="w-5 h-5 text-sakura-500" />
            <span className="font-semibold">实时监控总览</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-twilight-400 flex items-center gap-1">
              <Clock className="w-3.5 h-3.5" />
              自动刷新中
            </span>
            <StatusBadge
              status={metricsLoading ? "warning" : "ok"}
              label={metricsLoading ? "加载中" : "实时"}
            />
          </div>
        </div>
      </GlassCard>

      {metricsLoading && !metrics && (
        <LoadingSpinner text="正在拉取监控指标..." />
      )}
      {metricsError && !metrics && (
        <ErrorDisplay error={metricsError as Error} />
      )}

      {metrics && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="space-y-6"
        >
          {/* 系统状态大卡片 */}
          <motion.div variants={item}>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
              <StatCard
                title="World Tick"
                value={formatNum(metrics.world?.tick_total ?? 0)}
                icon="🌍"
                color="sakura"
              />
              <StatCard
                title="当前 Tick ID"
                value={`#${metrics.world?.current_tick_id ?? 0}`}
                icon="⏱️"
                color="sky"
              />
              <StatCard
                title="角色 Tick"
                value={formatNum(metrics.characters?.tick_total ?? 0)}
                icon="👥"
                color="twilight"
              />
              <StatCard
                title="活跃角色"
                value={metrics.system?.active_characters ?? 0}
                icon="✨"
                color="sakura"
              />
              <StatCard
                title="LLM 调用"
                value={formatNum(metrics.llm?.calls_total ?? 0)}
                icon="🤖"
                color="sky"
              />
              <StatCard
                title="LLM 成本"
                value={`$${(metrics.llm?.cost_total_usd ?? 0).toFixed(4)}`}
                icon="💰"
                color="twilight"
              />
            </div>
          </motion.div>

          {/* Redis & 错误状态 */}
          <motion.div variants={item}>
            <div className="grid md:grid-cols-4 gap-4">
              <div className="p-5 rounded-2xl bg-gradient-to-br from-white/40 to-sky-soft-100/40 border border-white/50 backdrop-blur-sm shadow-soft">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-sm text-twilight-400 font-medium flex items-center gap-1">
                    <Database className="w-4 h-4" />
                    Redis
                  </div>
                </div>
                <StatusBadge
                  status={
                    metrics.system?.redis_connected === 1 ? "ok" : "error"
                  }
                  label={
                    metrics.system?.redis_connected === 1 ? "已连接" : "已断开"
                  }
                />
              </div>
              <div className="p-5 rounded-2xl bg-gradient-to-br from-white/40 to-sakura-100/40 border border-white/50 backdrop-blur-sm shadow-soft">
                <div className="text-sm text-twilight-400 font-medium flex items-center gap-1 mb-2">
                  <AlertTriangle className="w-4 h-4" />
                  World 错误
                </div>
                <div
                  className={`text-xl font-bold ${metrics.world?.errors_total ? "text-red-500" : "text-emerald-500"}`}
                >
                  {metrics.world?.errors_total ?? 0}
                </div>
              </div>
              <div className="p-5 rounded-2xl bg-gradient-to-br from-white/40 to-twilight-100/40 border border-white/50 backdrop-blur-sm shadow-soft">
                <div className="text-sm text-twilight-400 font-medium flex items-center gap-1 mb-2">
                  <Zap className="w-4 h-4" />
                  LLM Token 总量
                </div>
                <div className="text-xl font-bold text-twilight-500">
                  {formatNum(metrics.llm?.tokens_total ?? 0)}
                </div>
              </div>
              <div className="p-5 rounded-2xl bg-gradient-to-br from-white/40 to-sakura-100/40 border border-white/50 backdrop-blur-sm shadow-soft">
                <div className="text-sm text-twilight-400 font-medium flex items-center gap-1 mb-2">
                  <Server className="w-4 h-4" />
                  Tick 平均耗时
                </div>
                <div className="text-xl font-bold text-sakura-500">
                  {metrics.world?.duration_count
                    ? `${((metrics.world?.duration_sum ?? 0) / metrics.world!.duration_count!).toFixed(3)}s`
                    : "—"}
                </div>
              </div>
            </div>
          </motion.div>

          {/* Action 执行统计 */}
          {actionChartData.length > 0 && (
            <motion.div variants={item}>
              <GlassCard hover={false}>
                <h3 className="font-semibold text-sakura-600 mb-1 flex items-center gap-2 text-lg">
                  <Cpu className="w-5 h-5" />
                  Action 执行统计（Top 10）
                </h3>
                <p className="text-xs text-twilight-400 mb-4 ml-7">
                  各 Action 的成功/失败次数
                </p>
                <div className="w-full h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={actionChartData}
                      margin={{ top: 10, right: 20, left: 0, bottom: 10 }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke="rgba(122,95,195,0.15)"
                      />
                      <XAxis
                        dataKey="name"
                        tick={{ fill: "#7a5fc3", fontSize: 11 }}
                        angle={-30}
                        textAnchor="end"
                        height={60}
                      />
                      <YAxis
                        tick={{ fill: "#7a5fc3", fontSize: 12 }}
                        tickFormatter={(v) => formatNum(v)}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "rgba(255,255,255,0.9)",
                          backdropFilter: "blur(10px)",
                          border: "1px solid rgba(255,143,171,0.3)",
                          borderRadius: "12px",
                          fontSize: "13px",
                        }}
                      />
                      <Legend />
                      <Bar
                        dataKey="成功"
                        stackId="a"
                        fill="#6FCF97"
                        radius={[0, 0, 0, 0]}
                      />
                      <Bar
                        dataKey="失败"
                        stackId="a"
                        fill="#FF8FAB"
                        radius={[8, 8, 0, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </GlassCard>
            </motion.div>
          )}

          {/* LLM Token 分布 & 角色 Tick */}
          <motion.div variants={item}>
            <div className="grid md:grid-cols-2 gap-4">
              {tokenPieData.length > 0 && (
                <GlassCard hover={false}>
                  <h3 className="font-semibold text-twilight-500 mb-1 flex items-center gap-2 text-lg">
                    <Cpu className="w-5 h-5" />
                    LLM Token 分布
                  </h3>
                  <p className="text-xs text-twilight-400 mb-4 ml-7">
                    按模型分组的 Token 使用量
                  </p>
                  <div className="w-full h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={tokenPieData}
                          cx="50%"
                          cy="50%"
                          labelLine={false}
                          label={({ name, percent }) =>
                            `${name} ${((percent ?? 0) * 100).toFixed(0)}%`
                          }
                          outerRadius={80}
                          fill="#8884d8"
                          dataKey="value"
                        >
                          {tokenPieData.map((_, index) => (
                            <Cell
                              key={`cell-${index}`}
                              fill={
                                PIE_COLORS[index % PIE_COLORS.length] ??
                                "#FF8FAB"
                              }
                            />
                          ))}
                        </Pie>
                        <Tooltip
                          formatter={(v) => [formatNum(Number(v)), "Tokens"]}
                          contentStyle={{
                            background: "rgba(255,255,255,0.9)",
                            backdropFilter: "blur(10px)",
                            border: "1px solid rgba(255,143,171,0.3)",
                            borderRadius: "12px",
                          }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                </GlassCard>
              )}

              {charTickData.length > 0 && (
                <GlassCard hover={false}>
                  <h3 className="font-semibold text-sakura-600 mb-1 flex items-center gap-2 text-lg">
                    <Activity className="w-5 h-5" />
                    角色 Tick 排行（Top 5）
                  </h3>
                  <p className="text-xs text-twilight-400 mb-4 ml-7">
                    各角色累计 Tick 执行次数
                  </p>
                  <div className="w-full h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart
                        data={charTickData}
                        layout="vertical"
                        margin={{ top: 5, right: 20, left: 20, bottom: 5 }}
                      >
                        <CartesianGrid
                          strokeDasharray="3 3"
                          stroke="rgba(122,95,195,0.15)"
                        />
                        <XAxis
                          type="number"
                          tick={{ fill: "#7a5fc3", fontSize: 12 }}
                          tickFormatter={(v) => formatNum(v)}
                        />
                        <YAxis
                          type="category"
                          dataKey="name"
                          tick={{ fill: "#7a5fc3", fontSize: 11 }}
                          width={80}
                        />
                        <Tooltip
                          contentStyle={{
                            background: "rgba(255,255,255,0.9)",
                            backdropFilter: "blur(10px)",
                            border: "1px solid rgba(255,143,171,0.3)",
                            borderRadius: "12px",
                          }}
                        />
                        <Bar
                          dataKey="ticks"
                          name="Tick 次数"
                          fill="#7EC8E3"
                          radius={[0, 8, 8, 0]}
                        />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </GlassCard>
              )}
            </div>
          </motion.div>

          {/* HTTP 请求 Top 5 */}
          {httpReqData.length > 0 && (
            <motion.div variants={item}>
              <GlassCard hover={false}>
                <h3 className="font-semibold text-twilight-500 mb-1 flex items-center gap-2 text-lg">
                  <Server className="w-5 h-5" />
                  HTTP 请求排行（Top 5）
                </h3>
                <p className="text-xs text-twilight-400 mb-4 ml-7">
                  请求次数最多的 API 路径
                </p>
                <div className="w-full h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={httpReqData}
                      layout="vertical"
                      margin={{ top: 5, right: 20, left: 40, bottom: 5 }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke="rgba(122,95,195,0.15)"
                      />
                      <XAxis
                        type="number"
                        tick={{ fill: "#7a5fc3", fontSize: 12 }}
                        tickFormatter={(v) => formatNum(v)}
                      />
                      <YAxis
                        type="category"
                        dataKey="name"
                        tick={{ fill: "#7a5fc3", fontSize: 10 }}
                        width={120}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "rgba(255,255,255,0.9)",
                          backdropFilter: "blur(10px)",
                          border: "1px solid rgba(255,143,171,0.3)",
                          borderRadius: "12px",
                        }}
                      />
                      <Bar
                        dataKey="请求数"
                        fill="#B19CD9"
                        radius={[0, 8, 8, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </GlassCard>
            </motion.div>
          )}

          {/* 消息处理统计 */}
          {metrics.messages?.by_platform &&
            Object.keys(metrics.messages.by_platform).length > 0 && (
              <motion.div variants={item}>
                <GlassCard hover={false}>
                  <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
                    <CheckCircle className="w-5 h-5" />
                    消息处理统计（按平台）
                  </h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    {Object.entries(metrics.messages.by_platform).map(
                      ([platform, counts]) => (
                        <div
                          key={platform}
                          className="p-4 rounded-xl bg-white/40 border border-white/50"
                        >
                          <div className="text-sm text-twilight-400 font-medium mb-2">
                            {platform}
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="flex items-center gap-1 text-emerald-600 text-sm font-semibold">
                              <CheckCircle className="w-3.5 h-3.5" />
                              {counts.success}
                            </span>
                            <span className="flex items-center gap-1 text-red-500 text-sm font-semibold">
                              <XCircle className="w-3.5 h-3.5" />
                              {counts.failed}
                            </span>
                          </div>
                        </div>
                      ),
                    )}
                  </div>
                </GlassCard>
              </motion.div>
            )}
        </motion.div>
      )}

      {/* 系统日志面板 */}
      <GlassCard hover={false}>
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <h3 className="font-semibold text-twilight-500 flex items-center gap-2 text-lg">
            <Terminal className="w-5 h-5 text-sakura-500" />
            系统日志
          </h3>
          <div className="flex items-center gap-2 flex-wrap">
            {/* 日志级别筛选 */}
            <div className="flex gap-1 p-1 rounded-xl bg-white/40 border border-white/50">
              {["all", "info", "warning", "error"].map((lv) => (
                <button
                  key={lv}
                  onClick={() => setLogLevel(lv === "all" ? undefined : lv)}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                    (logLevel ?? "all") === lv
                      ? "bg-sakura-100 text-sakura-600 shadow-sm"
                      : "text-twilight-400 hover:bg-white/50"
                  }`}
                >
                  {lv === "all" ? "全部" : lv}
                </button>
              ))}
            </div>
            {/* 日志行数 */}
            <select
              value={logLines}
              onChange={(e) => setLogLines(Number(e.target.value))}
              className="px-3 py-1.5 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-600 text-xs focus:outline-none focus:ring-2 focus:ring-sakura-400/30"
            >
              <option value={50}>50 行</option>
              <option value={100}>100 行</option>
              <option value={200}>200 行</option>
              <option value={500}>500 行</option>
            </select>
            <span className="text-xs text-twilight-400 flex items-center gap-1">
              <RefreshCw
                className="w-3 h-3 animate-spin"
                style={{ animationDuration: "5s" }}
              />
              自动刷新
            </span>
          </div>
        </div>

        {/* 日志来源 */}
        {logsData?.source && (
          <div className="text-xs text-twilight-300 mb-3 font-mono">
            📄 {logsData.source}
          </div>
        )}

        {/* 日志加载状态 */}
        {logsLoading && logs.length === 0 && (
          <LoadingSpinner text="正在加载日志..." />
        )}
        {logsError && <ErrorDisplay error={logsError as Error} />}

        {/* 日志列表 */}
        {logs.length > 0 && (
          <div className="max-h-[600px] overflow-y-auto rounded-xl bg-gray-900/90 border border-white/10 p-3 space-y-1 font-mono text-xs">
            {logs.map((log, idx) => {
              const level = (log.level ?? "info").toLowerCase();
              const lc = levelColors[level] ??
                levelColors.info ?? {
                  bg: "bg-gray-100",
                  text: "text-gray-500",
                  icon: "📝",
                };
              const event = String(
                log.event ?? log.message ?? JSON.stringify(log),
              );
              // 截取其他字段（排除已展示的）
              const otherFields = Object.entries(log)
                .filter(
                  ([k]) =>
                    !["timestamp", "level", "event", "message"].includes(k),
                )
                .slice(0, 4);
              return (
                <div
                  key={idx}
                  className={`flex items-start gap-2 px-2 py-1 rounded-lg hover:bg-white/5 ${lc.bg.replace("bg-", "bg-").replace("100", "900/30")}`}
                >
                  <span className="text-gray-500 shrink-0">
                    {formatLogTime(log.timestamp)}
                  </span>
                  <span className={`shrink-0 font-bold ${lc.text}`}>
                    {level.toUpperCase().padEnd(7)}
                  </span>
                  <span className="text-gray-200 break-all">
                    {event}
                    {otherFields.length > 0 && (
                      <span className="text-gray-500 ml-2">
                        {otherFields
                          .map(
                            ([k, v]) =>
                              `${k}=${typeof v === "string" ? v.slice(0, 50) : (JSON.stringify(v)?.slice(0, 50) ?? "")}`,
                          )
                          .join(" ")}
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {logs.length === 0 && !logsLoading && !logsError && (
          <div className="text-center py-12 text-twilight-400">
            <Terminal className="w-12 h-12 mx-auto mb-3 opacity-30" />
            <p>暂无日志记录</p>
          </div>
        )}
      </GlassCard>
    </div>
  );
}
