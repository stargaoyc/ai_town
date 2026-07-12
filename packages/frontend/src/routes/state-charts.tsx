import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import {
  Zap,
  Utensils,
  Users,
  Battery,
  User,
  Activity,
} from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  ProgressBar,
} from "@/components/ui";
import { useCharacters, useStateHistory } from "@/lib/queries";
import type { StateHistoryEntry } from "@/lib/api";

export const Route = createFileRoute("/state-charts")({
  component: StateChartsPage,
});

// 各状态维度配置：颜色、标签、图标
const dimensions = [
  {
    key: "stamina" as const,
    label: "精力",
    color: "#ff7a94", // 樱花粉
    icon: Zap,
    statColor: "sakura" as const,
    progressColor: "sakura" as const,
  },
  {
    key: "satiety" as const,
    label: "饱腹",
    color: "#5db8d5", // 天空蓝
    icon: Utensils,
    statColor: "sky" as const,
    progressColor: "sky" as const,
  },
  {
    key: "social_energy" as const,
    label: "社交精力",
    color: "#9b7fd1", // 暮光紫
    icon: Users,
    statColor: "twilight" as const,
    progressColor: "twilight" as const,
  },
  {
    key: "phone_battery" as const,
    label: "手机电量",
    color: "#10b981", // 绿色
    icon: Battery,
    statColor: "sakura" as const,
    progressColor: "sakura" as const,
  },
];

// 格式化时间戳为简短的时分显示
function formatTimeLabel(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(
      d.getDate(),
    ).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(
      d.getMinutes(),
    ).padStart(2, "0")}`;
  } catch {
    return dateStr;
  }
}

// Tooltip 中显示的完整时间
function formatFullTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("zh-CN");
  } catch {
    return dateStr;
  }
}

function StateChartsPage() {
  // 获取角色列表用于下拉选择
  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  // 当前选中的角色 ID
  const [characterId, setCharacterId] = useState<string>("");

  // 获取状态历史（最多 100 条）
  const { data, isLoading, error } = useStateHistory(characterId, 100);
  const history: StateHistoryEntry[] = data?.data ?? [];

  // 转换为 recharts 所需的数据格式（按时间正序排列）
  const chartData = useMemo(() => {
    return [...history]
      .sort(
        (a, b) =>
          new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime(),
      )
      .map((entry) => ({
        time: formatTimeLabel(entry.updated_at),
        fullTime: formatFullTime(entry.updated_at),
        stamina: entry.stamina,
        satiety: entry.satiety,
        social_energy: entry.social_energy,
        phone_battery: entry.phone_battery,
      }));
  }, [history]);

  // 当前最新状态（取最后一条记录）
  const currentState = useMemo(() => {
    if (history.length === 0) return null;
    const sorted = [...history].sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    );
    return sorted[0];
  }, [history]);

  return (
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="角色状态图表"
          subtitle="实时可视化角色精力、饱腹、社交精力与手机电量的变化趋势"
          icon="📊"
        />

        {/* 角色选择器 */}
        <GlassCard hover={false}>
          <div className="space-y-2">
            <label className="text-sm font-medium text-twilight-500 flex items-center gap-1.5">
              <User className="w-4 h-4 text-sakura-400" />
              选择角色
            </label>
            <select
              value={characterId}
              onChange={(e) => setCharacterId(e.target.value)}
              disabled={charsLoading}
              className="w-full px-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent focus:bg-white/80 transition-all disabled:opacity-50"
            >
              <option value="">
                {charsLoading ? "加载角色中..." : "请选择角色"}
              </option>
              {characters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}（{c.id}）
                </option>
              ))}
            </select>
          </div>
        </GlassCard>

        {/* 未选择角色提示 */}
        {!characterId && !charsLoading && (
          <EmptyState
            icon="📊"
            title="请先选择一个角色"
            subtitle="选择角色后将加载其状态历史数据并绘制实时图表"
          />
        )}

        {/* 加载与错误状态 */}
        {characterId && isLoading && (
          <LoadingSpinner text="正在加载状态历史数据..." />
        )}
        {characterId && error && <ErrorDisplay error={error} />}

        {/* 空数据提示 */}
        {characterId &&
          !isLoading &&
          !error &&
          history.length === 0 && (
            <EmptyState
              icon="📈"
              title="暂无状态历史数据"
              subtitle="角色 Tick 执行后会自动记录状态，请等待世界引擎运行一段时间后再查看"
            />
          )}

        {/* 状态数据展示 */}
        {characterId &&
          !isLoading &&
          !error &&
          history.length > 0 &&
          currentState && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="space-y-6"
            >
              {/* 顶部统计卡片：当前状态值 */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {dimensions.map((dim) => {
                  const Icon = dim.icon;
                  const value = (currentState[dim.key] as number) ?? 0;
                  return (
                    <StatCard
                      key={dim.key}
                      title={`当前${dim.label}`}
                      value={`${Math.round(value)}%`}
                      icon="📈"
                      color={dim.statColor}
                    />
                  );
                })}
              </div>

              {/* 当前状态进度条 */}
              <GlassCard hover={false}>
                <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
                  <Activity className="w-5 h-5" />
                  当前状态概览
                </h3>
                <div className="space-y-4">
                  {dimensions.map((dim) => {
                    const Icon = dim.icon;
                    const value = (currentState[dim.key] as number) ?? 0;
                    return (
                      <div key={dim.key}>
                        <div className="flex items-center justify-between mb-1.5 text-sm">
                          <span className="text-twilight-500 flex items-center gap-1.5 font-medium">
                            <Icon className="w-4 h-4" style={{ color: dim.color }} />
                            {dim.label}
                          </span>
                          <span
                            className="font-bold"
                            style={{ color: dim.color }}
                          >
                            {Math.round(value)}%
                          </span>
                        </div>
                        <ProgressBar
                          value={value}
                          max={100}
                          color={dim.progressColor}
                        />
                      </div>
                    );
                  })}
                </div>
                {/* 最新记录的附加信息 */}
                <div className="mt-4 grid grid-cols-2 md:grid-cols-3 gap-3 text-xs">
                  <div className="p-2.5 rounded-xl bg-white/40 border border-white/30">
                    <div className="text-twilight-400">情绪</div>
                    <div className="text-twilight-600 font-medium">
                      {currentState.mood || "—"}
                    </div>
                  </div>
                  <div className="p-2.5 rounded-xl bg-white/40 border border-white/30">
                    <div className="text-twilight-400">位置</div>
                    <div className="text-twilight-600 font-medium truncate">
                      {currentState.location || "—"}
                    </div>
                  </div>
                  <div className="p-2.5 rounded-xl bg-white/40 border border-white/30">
                    <div className="text-twilight-400">金钱</div>
                    <div className="text-twilight-600 font-medium">
                      {currentState.money?.toLocaleString() ?? "—"}
                    </div>
                  </div>
                </div>
              </GlassCard>

              {/* 多维度合并折线图 */}
              <GlassCard hover={false}>
                <h3 className="font-semibold text-sakura-600 mb-1 flex items-center gap-2 text-lg">
                  <Activity className="w-5 h-5" />
                  状态变化趋势
                </h3>
                <p className="text-xs text-twilight-400 mb-4 ml-7">
                  共 {chartData.length} 条历史记录 · X 轴为时间 · Y 轴为数值（0-100）
                </p>
                <div className="w-full h-96">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                      data={chartData}
                      margin={{ top: 10, right: 20, left: 0, bottom: 10 }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke="rgba(122,95,195,0.15)"
                      />
                      <XAxis
                        dataKey="time"
                        tick={{ fill: "#7a5fc3", fontSize: 11 }}
                        axisLine={{ stroke: "rgba(122,95,195,0.3)" }}
                        interval="preserveStartEnd"
                        minTickGap={30}
                      />
                      <YAxis
                        domain={[0, 100]}
                        tick={{ fill: "#7a5fc3", fontSize: 12 }}
                        axisLine={{ stroke: "rgba(122,95,195,0.3)" }}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "rgba(255,255,255,0.95)",
                          backdropFilter: "blur(10px)",
                          border: "1px solid rgba(255,143,171,0.3)",
                          borderRadius: "12px",
                          fontSize: "13px",
                        }}
                        labelFormatter={(_, payload) => {
                          const full = payload?.[0]?.payload?.fullTime;
                          return full ? `时间：${full}` : "";
                        }}
                        formatter={(value, name) => [
                          `${Math.round(Number(value))}%`,
                          name,
                        ]}
                      />
                      <Legend
                        wrapperStyle={{ fontSize: "13px", paddingTop: "10px" }}
                      />
                      {dimensions.map((dim) => (
                        <Line
                          key={dim.key}
                          type="monotone"
                          dataKey={dim.key}
                          name={dim.label}
                          stroke={dim.color}
                          strokeWidth={2.5}
                          dot={{ r: 2, fill: dim.color }}
                          activeDot={{ r: 5, strokeWidth: 1 }}
                        />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </GlassCard>

              {/* 图例颜色说明 */}
              <GlassCard hover={false}>
                <h3 className="font-semibold text-twilight-500 mb-3 flex items-center gap-2">
                  <span>🎨</span>
                  图表颜色说明
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {dimensions.map((dim) => {
                    const Icon = dim.icon;
                    return (
                      <div
                        key={dim.key}
                        className="flex items-center gap-2 p-2.5 rounded-xl bg-white/40 border border-white/30"
                      >
                        <div
                          className="w-4 h-4 rounded-full shrink-0"
                          style={{ backgroundColor: dim.color }}
                        />
                        <Icon
                          className="w-3.5 h-3.5 shrink-0"
                          style={{ color: dim.color }}
                        />
                        <span className="text-sm text-twilight-500 font-medium">
                          {dim.label}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </GlassCard>
            </motion.div>
          )}
      </div>
  );
}
