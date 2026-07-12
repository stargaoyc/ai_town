import { createFileRoute } from "@tanstack/react-router";
import { useState, useEffect, useCallback } from "react";
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
} from "recharts";
import { Activity, Database, DollarSign, Cpu, Clock } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  StatusBadge,
} from "@/components/ui";

export const Route = createFileRoute("/metrics")({
  component: MetricsPage,
});

// 解析后的指标数据结构
interface MetricsData {
  world_tick_total: number;
  world_tick_id: number;
  character_tick_total: number;
  llm_tokens_prompt: number;
  llm_tokens_completion: number;
  llm_cost_total_usd: number;
  redis_connected: number;
}

// 从 Prometheus 格式文本中解析指标
function parseMetrics(text: string): MetricsData {
  const data: MetricsData = {
    world_tick_total: 0,
    world_tick_id: 0,
    character_tick_total: 0,
    llm_tokens_prompt: 0,
    llm_tokens_completion: 0,
    llm_cost_total_usd: 0,
    redis_connected: 0,
  };

  const lines = text.split("\n");
  for (const line of lines) {
    // 跳过注释行和空行
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    // 解析 "metric_name{labels} value" 或 "metric_name value"
    const match = trimmed.match(/^([a-z_]+)(\{[^}]*\})?\s+([0-9.eE+-]+)$/);
    if (!match) continue;
    const [, name, labels, valueStr] = match;
    const value = parseFloat(valueStr ?? "");
    if (isNaN(value)) continue;

    // 后端指标以 ai_town_ 为前缀
    switch (name) {
      case "ai_town_world_tick_total":
        data.world_tick_total = value;
        break;
      case "ai_town_world_tick_id":
        data.world_tick_id = value;
        break;
      case "ai_town_character_tick_total":
        // 带标签的 Counter 需要累加
        data.character_tick_total += value;
        break;
      case "ai_town_llm_tokens_total":
        // 根据 label 区分 prompt / completion
        if (labels && labels.includes('"prompt"')) {
          data.llm_tokens_prompt += value;
        } else if (labels && labels.includes('"completion"')) {
          data.llm_tokens_completion += value;
        }
        break;
      case "ai_town_llm_cost_total_usd_total":
        data.llm_cost_total_usd += value;
        break;
      case "ai_town_redis_connected":
        data.redis_connected = value;
        break;
    }
  }
  return data;
}

// 格式化数字（千分位）
function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
  return n.toLocaleString();
}

function MetricsPage() {
  const [data, setData] = useState<MetricsData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // 获取 Prometheus 指标
  const fetchMetrics = useCallback(async () => {
    try {
      // 直接 fetch /metrics/ 端点，返回 Prometheus 格式文本
      const res = await fetch("/metrics/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      setData(parseMetrics(text));
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMetrics();
    // 每 5 秒自动刷新
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, [fetchMetrics]);

  // Token 柱状图数据
  const tokenChartData = data
    ? [
        { name: "Prompt", tokens: data.llm_tokens_prompt, fill: "#5db8d5" },
        {
          name: "Completion",
          tokens: data.llm_tokens_completion,
          fill: "#ff7a94",
        },
      ]
    : [];

  return (
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="Prometheus 指标面板"
          subtitle="实时监控小镇运行指标（每 5 秒刷新）"
          icon="📈"
        />

        {/* 最后更新时间 */}
        <GlassCard hover={false}>
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-2 text-twilight-500">
              <Activity className="w-5 h-5 text-sakura-500" />
              <span className="font-semibold">系统指标总览</span>
            </div>
            <div className="flex items-center gap-3">
              {lastUpdated && (
                <span className="text-xs text-twilight-400 flex items-center gap-1">
                  <Clock className="w-3.5 h-3.5" />
                  最后更新：{lastUpdated.toLocaleTimeString("zh-CN")}
                </span>
              )}
              <StatusBadge
                status={isLoading ? "warning" : "ok"}
                label={isLoading ? "加载中" : "实时"}
              />
            </div>
          </div>
        </GlassCard>

        {isLoading && !data && <LoadingSpinner text="正在拉取 Prometheus 指标..." />}
        {error && !data && <ErrorDisplay error={error} />}

        {data && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-6"
          >
            {/* 大数字指标卡片 */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
              <div title="ai_town_world_tick_total — 世界引擎累计运行的 Tick 总数">
                <StatCard
                  title="World Tick 总数"
                  value={formatNum(data.world_tick_total)}
                  icon="🌍"
                  color="sakura"
                />
              </div>
              <div title="ai_town_world_tick_id — 当前世界 Tick ID（自增序列号）">
                <StatCard
                  title="当前 Tick ID"
                  value={`#${data.world_tick_id}`}
                  icon="⏱️"
                  color="sky"
                />
              </div>
              <div title="ai_town_character_tick_total — 角色引擎累计处理的 Tick 总数">
                <StatCard
                  title="Character Tick 总数"
                  value={formatNum(data.character_tick_total)}
                  icon="👥"
                  color="twilight"
                />
              </div>
              <div title="ai_town_llm_cost_total_usd_total — LLM 调用累计成本（美元）">
                <StatCard
                  title="LLM 累计成本"
                  value={`$${data.llm_cost_total_usd.toFixed(4)}`}
                  icon="💰"
                  color="sakura"
                />
              </div>
              <div title="ai_town_redis_connected — Redis 连接状态（1=已连接, 0=断开）">
                <div className="p-5 rounded-2xl bg-gradient-to-br from-white/40 to-sky-soft-100/40 border border-white/50 backdrop-blur-sm shadow-soft h-full">
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-sm text-twilight-400 font-medium flex items-center gap-1">
                      <Database className="w-4 h-4" />
                      Redis 连接
                    </div>
                  </div>
                  <StatusBadge
                    status={data.redis_connected === 1 ? "ok" : "error"}
                    label={
                      data.redis_connected === 1 ? "已连接" : "已断开"
                    }
                  />
                </div>
              </div>
            </div>

            {/* LLM Token 使用量柱状图 */}
            <GlassCard hover={false}>
              <h3 className="font-semibold text-sakura-600 mb-1 flex items-center gap-2 text-lg">
                <Cpu className="w-5 h-5" />
                LLM Token 使用量
              </h3>
              <p className="text-xs text-twilight-400 mb-4 ml-7">
                按 Prompt / Completion 分组统计的累计 Token 消耗（llm_tokens_total）
              </p>
              <div className="w-full h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={tokenChartData}
                    margin={{ top: 10, right: 20, left: 0, bottom: 10 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(122,95,195,0.15)" />
                    <XAxis
                      dataKey="name"
                      tick={{ fill: "#7a5fc3", fontSize: 13 }}
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
                        border: "1px solid rgba(255,143,171,0.3)",
                        borderRadius: "12px",
                        fontSize: "13px",
                      }}
                      formatter={(v) => [formatNum(Number(v)), "Tokens"]}
                    />
                    <Legend />
                    <Bar
                      dataKey="tokens"
                      name="Token 数量"
                      radius={[8, 8, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </GlassCard>

            {/* 指标说明 */}
            <GlassCard hover={false}>
              <h3 className="font-semibold text-twilight-500 mb-3 flex items-center gap-2">
                <span>💡</span>
                指标说明
              </h3>
              <div className="grid md:grid-cols-2 gap-3 text-sm">
                <div className="flex items-start gap-2 p-3 rounded-xl bg-sakura-50/50">
                  <span className="text-sakura-500 font-mono text-xs mt-0.5 shrink-0">
                    ai_town_world_tick_total
                  </span>
                  <span className="text-twilight-400">
                    世界引擎累计运行的 Tick 总数
                  </span>
                </div>
                <div className="flex items-start gap-2 p-3 rounded-xl bg-sky-soft-50/50">
                  <span className="text-sky-soft-500 font-mono text-xs mt-0.5 shrink-0">
                    ai_town_world_tick_id
                  </span>
                  <span className="text-twilight-400">
                    当前世界 Tick ID（自增序列号）
                  </span>
                </div>
                <div className="flex items-start gap-2 p-3 rounded-xl bg-twilight-50/50">
                  <span className="text-twilight-500 font-mono text-xs mt-0.5 shrink-0">
                    ai_town_character_tick_total
                  </span>
                  <span className="text-twilight-400">
                    角色引擎累计处理的 Tick 总数
                  </span>
                </div>
                <div className="flex items-start gap-2 p-3 rounded-xl bg-sakura-50/50">
                  <span className="text-sakura-500 font-mono text-xs mt-0.5 shrink-0">
                    ai_town_llm_tokens_total
                  </span>
                  <span className="text-twilight-400">
                    LLM Token 使用量（按 prompt/completion 分组）
                  </span>
                </div>
                <div className="flex items-start gap-2 p-3 rounded-xl bg-sky-soft-50/50">
                  <span className="text-sky-soft-500 font-mono text-xs mt-0.5 shrink-0">
                    ai_town_llm_cost_total_usd_total
                  </span>
                  <span className="text-twilight-400">
                    LLM 调用累计成本（美元）
                  </span>
                </div>
                <div className="flex items-start gap-2 p-3 rounded-xl bg-twilight-50/50">
                  <span className="text-twilight-500 font-mono text-xs mt-0.5 shrink-0">
                    ai_town_redis_connected
                  </span>
                  <span className="text-twilight-400">
                    Redis 连接状态（1=已连接, 0=断开）
                  </span>
                </div>
              </div>
            </GlassCard>

            {/* 成本展示卡片 */}
            <GlassCard hover={false}>
              <div className="flex items-center justify-between flex-wrap gap-4">
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-sakura-100 to-twilight-100 flex items-center justify-center">
                    <DollarSign className="w-6 h-6 text-sakura-500" />
                  </div>
                  <div>
                    <div className="text-sm text-twilight-400">
                      LLM 累计成本（USD）
                    </div>
                    <div className="text-2xl font-bold gradient-text-sakura">
                      ${data.llm_cost_total_usd.toFixed(6)}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-sm text-twilight-400">Token 总量</div>
                  <div className="text-xl font-bold text-twilight-500">
                    {formatNum(data.llm_tokens_prompt + data.llm_tokens_completion)}
                  </div>
                </div>
              </div>
            </GlassCard>
          </motion.div>
        )}
      </div>
  );
}
