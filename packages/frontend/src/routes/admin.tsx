import { createFileRoute } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { Settings, Zap, CheckCircle2, XCircle } from "lucide-react";
import {
  GlassCard,
  ErrorDisplay,
  StatusBadge,
  StatCard,
  PageHeader,
  SkeletonList,
  AnimeButton,
} from "@/components/ui";
import { useAdminStatus, useForceTick } from "@/lib/queries";

export const Route = createFileRoute("/admin")({
  component: AdminPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.08 } },
};

const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0 },
};

function AdminPage() {
  const { data: status, isLoading, error } = useAdminStatus();
  const forceTick = useForceTick();

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader title="系统管理" subtitle="运维操作与状态监控" icon="⚙️" />

      {isLoading && <SkeletonList count={2} />}
      {error && <ErrorDisplay error={error} />}

      {status && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="space-y-6"
        >
          <motion.div variants={item}>
            <GlassCard>
              <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
                <Settings className="w-5 h-5" /> 系统状态
              </h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="p-4 rounded-2xl bg-gradient-to-br from-white/40 to-sakura-50/30 border border-white/20">
                  <div className="text-sm text-twilight-400 mb-2">
                    World Engine
                  </div>
                  <StatusBadge
                    status={status.world_engine.running ? "ok" : "error"}
                    label={status.world_engine.running ? "运行中" : "停止"}
                  />
                </div>
                <div className="p-4 rounded-2xl bg-gradient-to-br from-white/40 to-sky-soft-50/30 border border-white/20">
                  <div className="text-sm text-twilight-400 mb-2">
                    Character Engine
                  </div>
                  <StatusBadge
                    status={status.character_engine.available ? "ok" : "idle"}
                    label={
                      status.character_engine.available ? "可用" : "未启动"
                    }
                  />
                </div>
                <div className="p-4 rounded-2xl bg-gradient-to-br from-white/40 to-twilight-50/30 border border-white/20">
                  <div className="text-sm text-twilight-400 mb-2">Redis</div>
                  <StatusBadge
                    status={status.redis === "connected" ? "ok" : "error"}
                    label={status.redis === "connected" ? "已连接" : "断开"}
                  />
                </div>
                <StatCard
                  title="Tick ID"
                  value={`#${status.world_engine.tick_id}`}
                  icon="⏱️"
                />
              </div>
            </GlassCard>
          </motion.div>

          <motion.div variants={item}>
            <GlassCard>
              <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
                <Zap className="w-5 h-5" /> 运维操作
              </h3>
              <div className="flex items-center gap-4 flex-wrap">
                <AnimeButton
                  onClick={() => forceTick.mutate()}
                  disabled={forceTick.isPending}
                >
                  {forceTick.isPending ? "⏳ 执行中..." : "⚡ 强制 Tick"}
                </AnimeButton>
                {forceTick.isSuccess && (
                  <motion.span
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="flex items-center gap-1.5 text-sm text-emerald-600 px-3 py-1.5 rounded-xl bg-emerald-50/80 border border-emerald-200/50"
                  >
                    <CheckCircle2 className="w-4 h-4" />
                    Tick 已触发
                  </motion.span>
                )}
                {forceTick.isError && (
                  <motion.span
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="flex items-center gap-1.5 text-sm text-red-600 px-3 py-1.5 rounded-xl bg-red-50/80 border border-red-200/50"
                  >
                    <XCircle className="w-4 h-4" />
                    失败: {forceTick.error.message}
                  </motion.span>
                )}
              </div>
            </GlassCard>
          </motion.div>
        </motion.div>
      )}
    </div>
  );
}
