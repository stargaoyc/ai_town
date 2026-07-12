import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Clock, ChevronDown, Database, Hash } from "lucide-react";
import {
  NavLayout,
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  StatusBadge,
} from "@/components/ui";
import { useWorldSnapshots } from "@/lib/queries";
import type { SnapshotEntry } from "@/lib/api";

export const Route = createFileRoute("/snapshots")({
  component: SnapshotsPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.05 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

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

// 提取状态摘要：取 state 中的关键字段做简短描述
function summarizeState(state: Record<string, unknown>): string {
  const keys = Object.keys(state);
  if (keys.length === 0) return "空状态";
  const parts: string[] = [];
  // 常见字段优先展示
  const priorityKeys = [
    "tick_id",
    "world_time",
    "weather",
    "active_characters",
    "characters",
  ];
  for (const key of priorityKeys) {
    if (key in state) {
      const val = state[key];
      if (typeof val === "number" || typeof val === "string") {
        parts.push(`${key}: ${val}`);
      } else if (Array.isArray(val)) {
        parts.push(`${key}: [${val.length} 项]`);
      } else if (val && typeof val === "object") {
        parts.push(`${key}: {${Object.keys(val).length} 字段}`);
      }
    }
  }
  if (parts.length === 0) {
    parts.push(`共 ${keys.length} 个字段`);
  }
  return parts.join(" · ");
}

// 单个快照卡片（状态数据可折叠）
function SnapshotCard({ snapshot }: { snapshot: SnapshotEntry }) {
  const [expanded, setExpanded] = useState(false);
  const stateKeys = Object.keys(snapshot.state ?? {});

  return (
    <motion.div variants={item}>
      <GlassCard className="space-y-3" hover>
        {/* 顶部：tick_id + 创建时间 */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            {/* tick_id 标签 */}
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono font-medium border bg-twilight-100 text-twilight-500 border-twilight-200/50">
              <Hash className="w-3 h-3" />
              Tick {snapshot.tick_id}
            </span>
            <StatusBadge status="ok" label="快照" />
          </div>
          <span className="text-xs text-twilight-400 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatTime(snapshot.created_at)}
          </span>
        </div>

        {/* 状态摘要 */}
        <div className="flex items-start gap-2 text-sm text-twilight-600">
          <Database className="w-4 h-4 text-sakura-400 mt-0.5 shrink-0" />
          <span className="break-all">{summarizeState(snapshot.state)}</span>
        </div>

        {/* 状态数据 JSON 格式化展示（可折叠） */}
        {stateKeys.length > 0 && (
          <div className="rounded-xl bg-white/40 border border-white/40 overflow-hidden">
            <button
              onClick={() => setExpanded((v) => !v)}
              className="w-full flex items-center justify-between px-3 py-2 text-xs text-twilight-500 hover:bg-white/40 transition-colors"
            >
              <span className="font-medium">
                状态数据（JSON，{stateKeys.length} 个字段）
              </span>
              <motion.span animate={{ rotate: expanded ? 180 : 0 }}>
                <ChevronDown className="w-4 h-4" />
              </motion.span>
            </button>
            <AnimatePresence initial={false}>
              {expanded && (
                <motion.pre
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-x-auto px-3 py-2 text-xs text-twilight-600 bg-sakura-50/40 font-mono leading-relaxed max-h-80"
                >
                  {JSON.stringify(snapshot.state, null, 2)}
                </motion.pre>
              )}
            </AnimatePresence>
          </div>
        )}
      </GlassCard>
    </motion.div>
  );
}

function SnapshotsPage() {
  // 获取最近 50 条世界快照
  const { data, isLoading, error } = useWorldSnapshots(50);
  const snapshots = data?.data ?? [];

  // 统计：最新 / 最早快照 Tick ID
  const tickStats = useMemo(() => {
    if (snapshots.length === 0) return { latest: 0, earliest: 0 };
    const ticks = snapshots.map((s) => s.tick_id);
    return {
      latest: Math.max(...ticks),
      earliest: Math.min(...ticks),
    };
  }, [snapshots]);

  return (
    <NavLayout>
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="世界快照管理"
          subtitle="世界状态的完整快照记录，每 1000 Tick 自动创建一次"
          icon="📸"
        />

        {/* 顶部统计 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <StatCard
            title="快照总数"
            value={data?.total ?? snapshots.length}
            icon="📸"
            color="sakura"
          />
          <StatCard
            title="最新快照 Tick"
            value={tickStats.latest}
            icon="🆕"
            color="sky"
          />
          <StatCard
            title="最早快照 Tick"
            value={tickStats.earliest}
            icon="📜"
            color="twilight"
          />
        </div>

        {isLoading && <LoadingSpinner text="正在加载世界快照..." />}
        {error && <ErrorDisplay error={error} />}
        {!isLoading && !error && snapshots.length === 0 && (
          <EmptyState
            icon="📸"
            title="暂无世界快照"
            subtitle="暂无世界快照，每 1000 Tick 会自动创建一次完整快照"
          />
        )}

        {/* 快照列表 */}
        {snapshots.length > 0 && (
          <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="space-y-4"
          >
            {snapshots.map((snapshot) => (
              <SnapshotCard key={snapshot.id} snapshot={snapshot} />
            ))}
          </motion.div>
        )}
      </div>
    </NavLayout>
  );
}
