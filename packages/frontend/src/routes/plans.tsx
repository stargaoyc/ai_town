import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { ClipboardList, Clock, Flag, RefreshCw } from "lucide-react";
import {
  NavLayout,
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  StatusBadge,
  ProgressBar,
  AnimeButton,
} from "@/components/ui";
import { useCharacters, usePlans } from "@/lib/queries";

export const Route = createFileRoute("/plans")({
  component: PlansPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
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
  });
}

// 计划状态映射：active 绿色 / completed 蓝色 / pending 黄色
const planStatusMap: Record<
  string,
  {
    label: string;
    badge: "ok" | "warning" | "idle";
    custom?: string; // completed 无对应蓝色，使用自定义样式
    progress: number;
    barColor: "sakura" | "sky" | "twilight";
  }
> = {
  active: {
    label: "进行中",
    badge: "ok",
    progress: 60,
    barColor: "sakura",
  },
  completed: {
    label: "已完成",
    badge: "idle",
    custom: "blue",
    progress: 100,
    barColor: "sky",
  },
  pending: {
    label: "待开始",
    badge: "warning",
    progress: 0,
    barColor: "twilight",
  },
};

// 渲染计划状态徽章（completed 用自定义蓝色，其余用 StatusBadge）
function PlanStatusBadge({ status }: { status: string }) {
  const conf = planStatusMap[status] ?? {
    label: status || "未知",
    badge: "idle" as const,
    progress: 0,
    barColor: "twilight" as const,
  };
  if (conf.custom === "blue") {
    return (
      <motion.span
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-sky-soft-100/80 text-sky-soft-600 border border-sky-soft-200/60 shadow-sm"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-sky-soft-500 mr-1.5" />
        {conf.label}
      </motion.span>
    );
  }
  return <StatusBadge status={conf.badge} label={conf.label} />;
}

function PlansPage() {
  const [selectedCharacter, setSelectedCharacter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  const { data: plansData, isLoading, error, refetch, isFetching } =
    usePlans(selectedCharacter);
  const plans = plansData?.data ?? [];

  // 按状态过滤
  const filteredPlans = useMemo(() => {
    if (statusFilter === "all") return plans;
    return plans.filter((p) => p.status === statusFilter);
  }, [plans, statusFilter]);

  // 状态统计
  const stats = useMemo(() => {
    return {
      total: plans.length,
      active: plans.filter((p) => p.status === "active").length,
      completed: plans.filter((p) => p.status === "completed").length,
      pending: plans.filter((p) => p.status === "pending").length,
    };
  }, [plans]);

  const filterTabs = [
    { key: "all", label: "全部" },
    { key: "active", label: "进行中" },
    { key: "completed", label: "已完成" },
    { key: "pending", label: "待开始" },
  ];

  return (
    <NavLayout>
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="规划系统"
          subtitle="角色的目标规划与执行进度"
          icon="📋"
        />

        {/* 顶部统计 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard title="计划总数" value={stats.total} icon="📋" color="sakura" />
          <StatCard title="进行中" value={stats.active} icon="⚡" color="sakura" />
          <StatCard title="已完成" value={stats.completed} icon="✅" color="sky" />
          <StatCard title="待开始" value={stats.pending} icon="⏳" color="twilight" />
        </div>

        {/* 控制栏：角色选择 + 刷新 */}
        <GlassCard hover={false}>
          <div className="flex flex-col md:flex-row gap-4 md:items-end">
            <div className="flex-1">
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
            <AnimeButton
              variant="secondary"
              onClick={() => refetch()}
              disabled={!selectedCharacter || isFetching}
            >
              <span className="flex items-center gap-1.5">
                <RefreshCw
                  className={`w-4 h-4 ${isFetching ? "animate-spin" : ""}`}
                />
                刷新
              </span>
            </AnimeButton>
          </div>
        </GlassCard>

        {!selectedCharacter && (
          <EmptyState
            icon="👆"
            title="请先选择一个角色"
            subtitle="选择角色后将展示其计划列表"
          />
        )}

        {selectedCharacter && isLoading && (
          <LoadingSpinner text="正在加载计划列表..." />
        )}
        {selectedCharacter && !isLoading && error && (
          <ErrorDisplay error={error} />
        )}

        {selectedCharacter &&
          !isLoading &&
          !error &&
          plans.length === 0 && (
            <EmptyState
              icon="📋"
              title="暂无计划记录"
              subtitle="该角色还没有制定任何计划"
            />
          )}

        {/* 状态筛选标签 */}
        {selectedCharacter && !isLoading && !error && plans.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {filterTabs.map((tab) => {
              const count =
                tab.key === "all"
                  ? stats.total
                  : stats[tab.key as keyof typeof stats];
              const active = statusFilter === tab.key;
              return (
                <button
                  key={tab.key}
                  onClick={() => setStatusFilter(tab.key)}
                  className={`px-4 py-2 rounded-xl text-sm font-medium transition-all border ${
                    active
                      ? "bg-gradient-to-r from-sakura-400 to-sakura-500 text-white border-transparent shadow-md shadow-sakura-400/30"
                      : "bg-white/60 text-twilight-500 border-sakura-200/50 hover:bg-white/80"
                  }`}
                >
                  {tab.label}
                  <span
                    className={`ml-2 px-1.5 py-0.5 rounded-full text-xs ${
                      active ? "bg-white/30" : "bg-sakura-100/60"
                    }`}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {/* 计划列表 */}
        {filteredPlans.length > 0 && (
          <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="grid md:grid-cols-2 gap-4"
          >
            {filteredPlans.map((plan) => {
              const conf = planStatusMap[plan.status] ?? {
                label: plan.status || "未知",
                progress: 0,
                barColor: "twilight" as const,
              };
              const priority = plan.priority ?? 0;
              return (
                <motion.div key={plan.id} variants={item}>
                  <GlassCard className="space-y-3" hover>
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <PlanStatusBadge status={plan.status} />
                      <span className="text-xs text-twilight-400 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {formatTime(plan.created_at)}
                      </span>
                    </div>

                    <p className="text-sm text-twilight-700 leading-relaxed">
                      {plan.description}
                    </p>

                    {/* 优先级进度条 */}
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-twilight-400 flex items-center gap-1">
                          <Flag className="w-3 h-3" />
                          优先级
                        </span>
                        <span className="font-semibold text-sakura-600">
                          {priority}
                        </span>
                      </div>
                      <ProgressBar value={priority} max={10} color="sakura" />
                    </div>

                    {/* 计划进度条 */}
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-twilight-400">执行进度</span>
                        <span className="font-semibold text-twilight-500">
                          {conf.progress}%
                        </span>
                      </div>
                      <ProgressBar
                        value={conf.progress}
                        max={100}
                        color={conf.barColor}
                      />
                    </div>
                  </GlassCard>
                </motion.div>
              );
            })}
          </motion.div>
        )}

        {/* 说明 */}
        {selectedCharacter && !isLoading && plans.length > 0 && (
          <GlassCard hover={false}>
            <div className="flex items-start gap-3 text-sm text-twilight-400">
              <ClipboardList className="w-5 h-5 text-sakura-400 mt-0.5 shrink-0" />
              <div>
                <div className="font-medium text-twilight-500 mb-1">
                  计划说明
                </div>
                <ul className="space-y-1">
                  <li>• 状态：⚡进行中（绿色）、✅已完成（蓝色）、⏳待开始（黄色）</li>
                  <li>• 优先级范围 1-10，数值越高表示越紧急</li>
                  <li>• 使用上方标签可按状态筛选计划</li>
                </ul>
              </div>
            </div>
          </GlassCard>
        )}
      </div>
    </NavLayout>
  );
}
