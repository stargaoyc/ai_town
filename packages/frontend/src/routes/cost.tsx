import { createFileRoute } from "@tanstack/react-router";
import { useMemo } from "react";
import { motion } from "framer-motion";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { DollarSign, MessageSquare, Cpu, TrendingUp } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
} from "@/components/ui";
import { useMessageStats } from "@/lib/queries";

export const Route = createFileRoute("/cost")({
  component: CostPage,
});

// 饼图配色（樱花粉 / 天空蓝 / 暮光紫 色系）
const PIE_COLORS = [
  "#ff7a94",
  "#5db8d5",
  "#9b7fd1",
  "#ffa3b5",
  "#7ec8e3",
  "#b19cd9",
  "#f06680",
  "#3ba8c7",
  "#7a5fc3",
  "#ffcdd9",
];

// 格式化数字
function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
  return n.toLocaleString();
}

function CostPage() {
  const { data, isLoading, error } = useMessageStats();

  // 每日数据转换为图表可用格式
  const dailyData = useMemo(() => {
    if (!data?.by_day) return [];
    return Object.entries(data.by_day)
      .map(([date, v]) => ({
        date,
        messages: v.messages,
        tokens: v.tokens,
        cost: v.cost,
      }))
      .sort((a, b) => a.date.localeCompare(b.date));
  }, [data]);

  // 按角色分组的 Token 消耗（饼图）
  const characterTokenData = useMemo(() => {
    if (!data?.by_character) return [];
    return Object.entries(data.by_character)
      .map(([name, v]) => ({
        name,
        value: v.tokens,
        messages: v.messages,
        cost: v.cost,
      }))
      .sort((a, b) => b.value - a.value);
  }, [data]);

  const hasData = data && (data.total_messages > 0 || dailyData.length > 0);

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="LLM 成本仪表盘"
        subtitle="消息量、Token 消耗与成本分析"
        icon="💰"
        backTo="/admin"
        backLabel="返回管理"
      />

      {isLoading && <LoadingSpinner text="正在加载统计数据..." />}
      {error && <ErrorDisplay error={error} />}

      {data && !hasData && (
        <EmptyState
          icon="📊"
          title="暂无统计数据"
          subtitle="当有消息交互后，成本与 Token 统计将显示在这里"
        />
      )}

      {data && hasData && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-6"
        >
          {/* 顶部统计卡片 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <StatCard
              title="总消息数"
              value={formatNum(data.total_messages)}
              icon="📨"
              color="sakura"
            />
            <StatCard
              title="总 Token 数"
              value={formatNum(data.total_tokens)}
              icon="🧮"
              color="sky"
            />
            <StatCard
              title="总成本（USD）"
              value={`$${data.total_cost.toFixed(4)}`}
              icon="💸"
              color="twilight"
            />
          </div>

          {/* 每日消息数折线图 */}
          {dailyData.length > 0 && (
            <GlassCard hover={false}>
              <h3 className="font-semibold text-sakura-600 mb-1 flex items-center gap-2 text-lg">
                <MessageSquare className="w-5 h-5" />
                每日消息数
              </h3>
              <p className="text-xs text-twilight-400 mb-4 ml-7">
                按日期统计的消息发送数量趋势
              </p>
              <div className="w-full h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={dailyData}
                    margin={{ top: 10, right: 20, left: 0, bottom: 10 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="rgba(122,95,195,0.15)"
                    />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: "#7a5fc3", fontSize: 12 }}
                      axisLine={{ stroke: "rgba(122,95,195,0.3)" }}
                    />
                    <YAxis
                      tick={{ fill: "#7a5fc3", fontSize: 12 }}
                      axisLine={{ stroke: "rgba(122,95,195,0.3)" }}
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
                    <Line
                      type="monotone"
                      dataKey="messages"
                      name="消息数"
                      stroke="#ff7a94"
                      strokeWidth={3}
                      dot={{ fill: "#ff7a94", r: 4 }}
                      activeDot={{ r: 6 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </GlassCard>
          )}

          {/* 每日 Token 消耗柱状图 */}
          {dailyData.length > 0 && (
            <GlassCard hover={false}>
              <h3 className="font-semibold text-sky-soft-500 mb-1 flex items-center gap-2 text-lg">
                <Cpu className="w-5 h-5" />
                每日 Token 消耗
              </h3>
              <p className="text-xs text-twilight-400 mb-4 ml-7">
                按日期统计的 LLM Token 消耗量
              </p>
              <div className="w-full h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={dailyData}
                    margin={{ top: 10, right: 20, left: 0, bottom: 10 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="rgba(122,95,195,0.15)"
                    />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: "#7a5fc3", fontSize: 12 }}
                      axisLine={{ stroke: "rgba(122,95,195,0.3)" }}
                    />
                    <YAxis
                      tick={{ fill: "#7a5fc3", fontSize: 12 }}
                      axisLine={{ stroke: "rgba(122,95,195,0.3)" }}
                      tickFormatter={(v) => formatNum(v)}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "rgba(255,255,255,0.9)",
                        backdropFilter: "blur(10px)",
                        border: "1px solid rgba(126,200,227,0.3)",
                        borderRadius: "12px",
                        fontSize: "13px",
                      }}
                      formatter={(v) => [formatNum(Number(v)), "Tokens"]}
                    />
                    <Bar
                      dataKey="tokens"
                      name="Token 数量"
                      fill="#5db8d5"
                      radius={[8, 8, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </GlassCard>
          )}

          {/* 每日成本折线图 */}
          {dailyData.length > 0 && (
            <GlassCard hover={false}>
              <h3 className="font-semibold text-twilight-500 mb-1 flex items-center gap-2 text-lg">
                <DollarSign className="w-5 h-5" />
                每日成本
              </h3>
              <p className="text-xs text-twilight-400 mb-4 ml-7">
                按日期统计的 LLM 调用成本（美元）
              </p>
              <div className="w-full h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={dailyData}
                    margin={{ top: 10, right: 20, left: 0, bottom: 10 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="rgba(122,95,195,0.15)"
                    />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: "#7a5fc3", fontSize: 12 }}
                      axisLine={{ stroke: "rgba(122,95,195,0.3)" }}
                    />
                    <YAxis
                      tick={{ fill: "#7a5fc3", fontSize: 12 }}
                      axisLine={{ stroke: "rgba(122,95,195,0.3)" }}
                      tickFormatter={(v) => `$${v.toFixed(2)}`}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "rgba(255,255,255,0.9)",
                        backdropFilter: "blur(10px)",
                        border: "1px solid rgba(177,156,217,0.3)",
                        borderRadius: "12px",
                        fontSize: "13px",
                      }}
                      formatter={(v) => [`$${Number(v).toFixed(4)}`, "成本"]}
                    />
                    <Line
                      type="monotone"
                      dataKey="cost"
                      name="成本 (USD)"
                      stroke="#9b7fd1"
                      strokeWidth={3}
                      dot={{ fill: "#9b7fd1", r: 4 }}
                      activeDot={{ r: 6 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </GlassCard>
          )}

          {/* 按角色分组的 Token 消耗饼图 */}
          {characterTokenData.length > 0 && (
            <GlassCard hover={false}>
              <h3 className="font-semibold text-sakura-600 mb-1 flex items-center gap-2 text-lg">
                <TrendingUp className="w-5 h-5" />
                按角色分组的 Token 消耗
              </h3>
              <p className="text-xs text-twilight-400 mb-4 ml-7">
                各角色累计消耗的 Token 占比
              </p>
              <div className="w-full h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={characterTokenData}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      outerRadius={90}
                      innerRadius={40}
                      label={(entry) =>
                        `${entry.name}: ${formatNum(entry.value as number)}`
                      }
                      labelLine={false}
                    >
                      {characterTokenData.map((_, index) => (
                        <Cell
                          key={`cell-${index}`}
                          fill={
                            PIE_COLORS[index % PIE_COLORS.length] ?? "#ff7a94"
                          }
                        />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: "rgba(255,255,255,0.9)",
                        backdropFilter: "blur(10px)",
                        border: "1px solid rgba(255,143,171,0.3)",
                        borderRadius: "12px",
                        fontSize: "13px",
                      }}
                      formatter={(v, _name, entry) => [
                        formatNum(Number(v)),
                        entry?.payload?.name ?? "Token",
                      ]}
                    />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </GlassCard>
          )}

          {/* 成本估算说明 */}
          <GlassCard hover={false}>
            <h3 className="font-semibold text-twilight-500 mb-3 flex items-center gap-2">
              <span>💡</span>
              成本估算说明
            </h3>
            <div className="grid md:grid-cols-2 gap-4 text-sm">
              <div className="p-4 rounded-2xl bg-sakura-50/50 border border-sakura-100/50">
                <div className="font-semibold text-sakura-600 mb-2 flex items-center gap-2">
                  <span>📝</span> Prompt 输入
                </div>
                <p className="text-twilight-400">
                  输入 Token 按 LLM 供应商单价计费。不同模型单价不同，
                  通常输入单价低于输出单价。
                </p>
              </div>
              <div className="p-4 rounded-2xl bg-sky-soft-50/50 border border-sky-soft-100/50">
                <div className="font-semibold text-sky-soft-500 mb-2 flex items-center gap-2">
                  <span>✨</span> Completion 输出
                </div>
                <p className="text-twilight-400">
                  输出 Token 单价通常为输入的 2-4 倍。 成本 = Prompt Tokens ×
                  输入单价 + Completion Tokens × 输出单价。
                </p>
              </div>
            </div>
            <div className="mt-3 p-3 rounded-xl bg-twilight-50/50 text-xs text-twilight-400">
              💡 提示：以上统计基于后端{" "}
              <code className="px-1 rounded bg-white/50 text-twilight-500">
                /messages/stats
              </code>{" "}
              接口返回的累计数据， 实际成本以 LLM 供应商账单为准。
            </div>
          </GlassCard>
        </motion.div>
      )}
    </div>
  );
}
