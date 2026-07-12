import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { Clock, Layers } from "lucide-react";
import {
  NavLayout,
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
} from "@/components/ui";
import { useWorldEventsRange } from "@/lib/queries";
import type { WorldEventEntry } from "@/lib/api";

export const Route = createFileRoute("/events")({
  component: EventsPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.04 } },
};

const item = {
  hidden: { opacity: 0, x: -20 },
  show: { opacity: 1, x: 0 },
};

// 事件类型配置：图标 + 标签 + 颜色
const eventTypeConfig: Record<
  string,
  { icon: string; label: string; color: string; dotColor: string }
> = {
  time: {
    icon: "🕐",
    label: "时间",
    color: "bg-sky-soft-100 text-sky-soft-600 border-sky-soft-200/50",
    dotColor: "from-sky-soft-300 to-sky-soft-400",
  },
  weather: {
    icon: "🌤️",
    label: "天气",
    color: "bg-twilight-100 text-twilight-500 border-twilight-200/50",
    dotColor: "from-twilight-300 to-twilight-400",
  },
  scene: {
    icon: "📍",
    label: "场景",
    color: "bg-sakura-100 text-sakura-600 border-sakura-200/50",
    dotColor: "from-sakura-400 to-sakura-500",
  },
  resource: {
    icon: "📦",
    label: "资源",
    color: "bg-amber-100 text-amber-600 border-amber-200/50",
    dotColor: "from-amber-300 to-amber-400",
  },
  event: {
    icon: "✨",
    label: "事件",
    color: "bg-emerald-100 text-emerald-600 border-emerald-200/50",
    dotColor: "from-emerald-300 to-emerald-400",
  },
};

// 筛选标签
const filterTabs = [
  { key: "all", label: "全部", icon: "📋" },
  { key: "time", label: "时间", icon: "🕐" },
  { key: "weather", label: "天气", icon: "🌤️" },
  { key: "scene", label: "场景", icon: "📍" },
  { key: "resource", label: "资源", icon: "📦" },
  { key: "event", label: "事件", icon: "✨" },
];

// 格式化时间
function formatTime(dateStr: string): string {
  return new Date(dateStr).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

// 将 payload 摘要为简短字符串
function summarizePayload(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload);
  if (entries.length === 0) return "（无附加数据）";
  // 取前 3 个键值对拼接
  const parts = entries.slice(0, 3).map(([k, v]) => {
    const val = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
    const short = val.length > 40 ? `${val.slice(0, 40)}...` : val;
    return `${k}: ${short}`;
  });
  const more = entries.length > 3 ? ` (+${entries.length - 3})` : "";
  return parts.join("  ·  ") + more;
}

function EventsPage() {
  const [typeFilter, setTypeFilter] = useState<string>("all");

  const { data, isLoading, error } = useWorldEventsRange({ limit: 100 });
  const events = data?.data ?? [];

  // 按类型过滤
  const filteredEvents = useMemo(() => {
    if (typeFilter === "all") return events;
    return events.filter((e) => e.event_type === typeFilter);
  }, [events, typeFilter]);

  // 按 tick_id 分组（降序），每个 Tick 下事件聚合
  const groupedByTick = useMemo(() => {
    const map = new Map<number, WorldEventEntry[]>();
    for (const ev of filteredEvents) {
      const arr = map.get(ev.tick_id) ?? [];
      arr.push(ev);
      map.set(ev.tick_id, arr);
    }
    // tick_id 降序排列
    return Array.from(map.entries()).sort((a, b) => b[0] - a[0]);
  }, [filteredEvents]);

  // 类型统计
  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const ev of events) {
      counts[ev.event_type] = (counts[ev.event_type] ?? 0) + 1;
    }
    return counts;
  }, [events]);

  return (
    <NavLayout>
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="世界事件时间线"
          subtitle="按 Tick 聚合的世界事件流"
          icon="⏱️"
        />

        {/* 顶部统计 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            title="事件总数"
            value={events.length}
            icon="📊"
            color="sakura"
          />
          <StatCard
            title="Tick 数"
            value={groupedByTick.length}
            icon="⏱️"
            color="sky"
          />
          <StatCard
            title="已筛选"
            value={filteredEvents.length}
            icon="🔍"
            color="twilight"
          />
          <StatCard
            title="类型数"
            value={Object.keys(typeCounts).length}
            icon="🏷️"
            color="sakura"
          />
        </div>

        {/* 类型筛选标签 */}
        <GlassCard hover={false}>
          <div className="flex flex-wrap gap-2">
            {filterTabs.map((tab) => {
              const count =
                tab.key === "all"
                  ? events.length
                  : typeCounts[tab.key] ?? 0;
              const active = typeFilter === tab.key;
              return (
                <button
                  key={tab.key}
                  onClick={() => setTypeFilter(tab.key)}
                  className={`px-4 py-2 rounded-xl text-sm font-medium transition-all border flex items-center gap-1.5 ${
                    active
                      ? "bg-gradient-to-r from-sakura-400 to-sakura-500 text-white border-transparent shadow-md shadow-sakura-400/30"
                      : "bg-white/60 text-twilight-500 border-sakura-200/50 hover:bg-white/80"
                  }`}
                >
                  <span>{tab.icon}</span>
                  {tab.label}
                  <span
                    className={`ml-1 px-1.5 py-0.5 rounded-full text-xs ${
                      active ? "bg-white/30" : "bg-sakura-100/60"
                    }`}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
        </GlassCard>

        {isLoading && <LoadingSpinner text="正在加载世界事件..." />}
        {error && <ErrorDisplay error={error} />}

        {!isLoading && !error && events.length === 0 && (
          <EmptyState
            icon="⏱️"
            title="暂无世界事件"
            subtitle="世界引擎运行后将在此产生事件记录"
          />
        )}

        {!isLoading && !error && events.length > 0 && filteredEvents.length === 0 && (
          <EmptyState
            icon="🔍"
            title="该类型暂无事件"
            subtitle="尝试切换为「全部」查看"
          />
        )}

        {/* 时间线（按 Tick 分组） */}
        {groupedByTick.length > 0 && (
          <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="relative"
          >
            {/* 垂直时间轴线 */}
            <div className="absolute left-5 top-0 bottom-0 w-0.5 bg-gradient-to-b from-sakura-300 via-twilight-300 to-sky-soft-300" />

            <div className="space-y-6">
              {groupedByTick.map(([tickId, tickEvents]) => (
                <motion.div key={tickId} variants={item} className="relative pl-14">
                  {/* Tick 节点 */}
                  <div className="absolute left-0 top-2 w-11 h-11 rounded-2xl bg-gradient-to-br from-sakura-400 to-twilight-400 flex items-center justify-center text-white font-bold text-sm shadow-lg border-2 border-white/60">
                    <Layers className="w-5 h-5" />
                  </div>

                  <div className="space-y-3">
                    {/* Tick 标题 */}
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-twilight-600">
                        Tick #{tickId}
                      </span>
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-twilight-100 text-twilight-500 border border-twilight-200/50">
                        {tickEvents.length} 个事件
                      </span>
                    </div>

                    {/* 该 Tick 下的所有事件 */}
                    <div className="space-y-2">
                      {tickEvents.map((ev) => {
                        const conf = eventTypeConfig[ev.event_type] ?? {
                          icon: "📝",
                          label: ev.event_type,
                          color: "bg-gray-100 text-gray-500 border-gray-200/50",
                          dotColor: "from-gray-300 to-gray-400",
                        };
                        return (
                          <GlassCard
                            key={ev.id}
                            className="!p-4 space-y-2"
                            hover
                          >
                            <div className="flex items-center justify-between gap-2 flex-wrap">
                              <div className="flex items-center gap-2">
                                <span className="text-lg">{conf.icon}</span>
                                <span
                                  className={`px-2 py-0.5 rounded-full text-xs font-medium border ${conf.color}`}
                                >
                                  {conf.label}
                                </span>
                                {ev.event_key && (
                                  <span className="text-xs text-twilight-400 font-mono">
                                    {ev.event_key}
                                  </span>
                                )}
                              </div>
                              <span className="text-xs text-twilight-400 flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                {formatTime(ev.created_at)}
                              </span>
                            </div>
                            {/* payload 摘要 */}
                            <p className="text-sm text-twilight-600 leading-relaxed break-all">
                              {summarizePayload(ev.payload)}
                            </p>
                          </GlassCard>
                        );
                      })}
                    </div>
                  </div>
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}

        {/* 说明 */}
        {!isLoading && events.length > 0 && (
          <GlassCard hover={false}>
            <div className="flex items-start gap-3 text-sm text-twilight-400">
              <Layers className="w-5 h-5 text-sakura-400 mt-0.5 shrink-0" />
              <div>
                <div className="font-medium text-twilight-500 mb-1">
                  事件说明
                </div>
                <ul className="space-y-1">
                  <li>• 🕐时间 / 🌤️天气 / 📍场景 / 📦资源 / ✨事件 五类</li>
                  <li>• 事件按 Tick 分组聚合，相同 Tick 下的事件归并展示</li>
                  <li>• 使用上方标签可按事件类型筛选</li>
                </ul>
              </div>
            </div>
          </GlassCard>
        )}
      </div>
    </NavLayout>
  );
}
