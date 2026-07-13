import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Clock, ChevronDown, Activity, Hash } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  StatusBadge,
} from "@/components/ui";
import { useCharacters, useCharacterActions } from "@/lib/queries";

export const Route = createFileRoute("/actions")({
  component: ActionsPage,
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
  if (!dateStr) return "—";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

// action_id 标签配色（循环取色）
const actionColorPalette = [
  "bg-sakura-100 text-sakura-600 border-sakura-200/50",
  "bg-sky-soft-100 text-sky-soft-600 border-sky-soft-200/50",
  "bg-twilight-100 text-twilight-500 border-twilight-200/50",
  "bg-emerald-100 text-emerald-600 border-emerald-200/50",
  "bg-amber-100 text-amber-600 border-amber-200/50",
];

// 根据字符串哈希选取颜色
function colorForActionId(actionId: string): string {
  let hash = 0;
  for (let i = 0; i < actionId.length; i++) {
    hash = (hash * 31 + actionId.charCodeAt(i)) | 0;
  }
  return (
    actionColorPalette[Math.abs(hash) % actionColorPalette.length] ??
    actionColorPalette[0] ??
    ""
  );
}

// 单条行为日志（结果可折叠）
function ActionLogItem({
  actionId,
  actionName,
  duration,
  result,
  createdAt,
}: {
  actionId: string;
  actionName?: string;
  duration?: number;
  result?: Record<string, unknown>;
  createdAt: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasResult = result && Object.keys(result).length > 0;
  const colorClass = colorForActionId(actionId);

  // 判断结果状态：含 error/success 字段时显示徽章
  const resultStatus = (): "ok" | "error" | "idle" | null => {
    if (!result) return null;
    if (result.success === true) return "ok";
    if (result.success === false || result.error) return "error";
    return null;
  };
  const status = resultStatus();

  return (
    <motion.div variants={item}>
      <GlassCard className="space-y-3" hover>
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            {/* action_id 标签 */}
            <span
              className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-mono font-medium border ${colorClass}`}
            >
              <Hash className="w-3 h-3" />
              {actionId}
            </span>
            {actionName && (
              <span className="text-sm font-semibold text-twilight-600">
                {actionName}
              </span>
            )}
            {status && (
              <StatusBadge
                status={status}
                label={status === "ok" ? "成功" : "失败"}
              />
            )}
          </div>
          <span className="text-xs text-twilight-400 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatTime(createdAt)}
          </span>
        </div>

        {/* 持续时间 */}
        {duration !== undefined && duration !== null && (
          <div className="flex items-center gap-2 text-xs text-twilight-400">
            <Activity className="w-3.5 h-3.5 text-sakura-400" />
            <span>
              持续时间：
              <span className="font-semibold text-twilight-500 ml-1">
                {duration}
              </span>
              {typeof duration === "number" && duration >= 60
                ? ` 分钟（约 ${(duration / 60).toFixed(1)} 小时）`
                : " 秒"}
            </span>
          </div>
        )}

        {/* 结果 JSON（可折叠） */}
        {hasResult && (
          <div className="rounded-xl bg-white/40 border border-white/40 overflow-hidden">
            <button
              onClick={() => setExpanded((v) => !v)}
              className="w-full flex items-center justify-between px-3 py-2 text-xs text-twilight-500 hover:bg-white/40 transition-colors"
            >
              <span className="font-medium">结果详情（JSON）</span>
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
                  className="overflow-x-auto px-3 py-2 text-xs text-twilight-600 bg-sakura-50/40 font-mono leading-relaxed max-h-64"
                >
                  {JSON.stringify(result, null, 2)}
                </motion.pre>
              )}
            </AnimatePresence>
          </div>
        )}
      </GlassCard>
    </motion.div>
  );
}

function ActionsPage() {
  const [selectedCharacter, setSelectedCharacter] = useState("");

  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  const {
    data: actionsData,
    isLoading,
    error,
  } = useCharacterActions(selectedCharacter, 100);
  const actions = actionsData?.data ?? [];

  // 唯一 action_id 数量
  const uniqueActionCount = new Set(actions.map((a) => a.action_id)).size;

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="角色行为日志"
        subtitle="角色执行的行为记录与结果详情"
        icon="📝"
        backTo="/admin"
        backLabel="返回管理"
      />

      {/* 顶部统计 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          title="行为总数"
          value={actions.length}
          icon="📝"
          color="sakura"
        />
        <StatCard
          title="行为类型"
          value={uniqueActionCount}
          icon="🎯"
          color="sky"
        />
        <StatCard
          title="角色数"
          value={characters.length}
          icon="👥"
          color="twilight"
        />
      </div>

      {/* 角色选择器 */}
      <GlassCard hover={false}>
        <div>
          <label className="block text-sm text-twilight-500 font-medium mb-2">
            选择角色
          </label>
          {charsLoading ? (
            <div className="text-sm text-twilight-400">加载角色中...</div>
          ) : (
            <select
              value={selectedCharacter}
              onChange={(e) => setSelectedCharacter(e.target.value)}
              className="w-full px-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent focus:bg-white/80 transition-all"
            >
              <option value="">— 请选择角色 —</option>
              {characters.map((char) => (
                <option key={char.id} value={char.id}>
                  {char.name}（{char.id}）
                </option>
              ))}
            </select>
          )}
        </div>
      </GlassCard>

      {!selectedCharacter && (
        <EmptyState
          icon="👆"
          title="请先选择一个角色"
          subtitle="选择角色后将展示其行为日志"
        />
      )}

      {selectedCharacter && isLoading && (
        <LoadingSpinner text="正在加载行为日志..." />
      )}
      {selectedCharacter && !isLoading && error && (
        <ErrorDisplay error={error} />
      )}

      {selectedCharacter && !isLoading && !error && actions.length === 0 && (
        <EmptyState
          icon="📝"
          title="暂无行为日志"
          subtitle="该角色还没有执行任何行为记录"
        />
      )}

      {/* 行为日志列表 */}
      {actions.length > 0 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="space-y-3"
        >
          {actions.map((action, idx) => (
            <ActionLogItem
              key={action.id ?? idx}
              actionId={action.action_id}
              {...(action.action_name !== undefined && {
                actionName: action.action_name,
              })}
              {...((action.duration_minutes ?? action.duration !== undefined)
                ? { duration: action.duration_minutes ?? action.duration }
                : {})}
              {...(action.result !== undefined && action.result !== null
                ? { result: action.result }
                : {})}
              createdAt={action.timestamp ?? action.created_at ?? ""}
            />
          ))}
        </motion.div>
      )}

      {/* 说明 */}
      {selectedCharacter && !isLoading && actions.length > 0 && (
        <GlassCard hover={false}>
          <div className="flex items-start gap-3 text-sm text-twilight-400">
            <Activity className="w-5 h-5 text-sakura-400 mt-0.5 shrink-0" />
            <div>
              <div className="font-medium text-twilight-500 mb-1">日志说明</div>
              <ul className="space-y-1">
                <li>• action_id 以彩色标签展示，不同颜色区分行为类型</li>
                <li>• 点击「结果详情」可展开/折叠 JSON 结果</li>
                <li>• 含 success/error 字段的结果会显示状态徽章</li>
              </ul>
            </div>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
