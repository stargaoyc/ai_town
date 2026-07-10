import { createFileRoute, Link } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { Activity } from "lucide-react";
import {
  GlassCard,
  StatCard,
  ErrorDisplay,
  StatusBadge,
  PageHeader,
  SkeletonList,
} from "@/components/ui";
import { useHealth, useWorld, useCharacters } from "@/lib/queries";

export const Route = createFileRoute("/")({
  component: HomePage,
});

const container = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.08 },
  },
};

const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0 },
};

function HomePage() {
  const health = useHealth();
  const world = useWorld();
  const characters = useCharacters({ active_only: true });

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="Dashboard"
        subtitle="二次元 AI 小镇陪伴智能体"
        icon="🌸"
      />

      <GlassCard>
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div>
            <h2 className="text-xl font-semibold text-twilight-500 flex items-center gap-2">
              <Activity className="w-5 h-5 text-sakura-500" />
              系统状态
            </h2>
            <p className="text-sm text-twilight-400 mt-1">
              实时监控小镇运行状况
            </p>
          </div>
          <StatusBadge
            status={health.data?.status === "ok" ? "ok" : "error"}
            label={health.data?.status === "ok" ? "🟢 运行中" : "🔴 异常"}
          />
        </div>
      </GlassCard>

      {health.isLoading && <SkeletonList count={1} />}
      {health.error && <ErrorDisplay error={health.error} />}

      {health.data && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="grid grid-cols-2 md:grid-cols-4 gap-4"
        >
          <motion.div variants={item}>
            <StatCard
              title="World Tick"
              value={`#${health.data.world_tick}`}
              icon="⏱️"
              color="sakura"
            />
          </motion.div>
          <motion.div variants={item}>
            <StatCard
              title="Redis"
              value={health.data.redis === "connected" ? "已连接" : "断开"}
              icon="🔴"
              color="sky"
            />
          </motion.div>
          <motion.div variants={item}>
            <StatCard
              title="天气"
              value={world.data?.weather ?? "—"}
              icon="🌤️"
              color="twilight"
            />
          </motion.div>
          <motion.div variants={item}>
            <StatCard
              title="活跃角色"
              value={characters.data?.total ?? 0}
              icon="👥"
              color="sakura"
            />
          </motion.div>
        </motion.div>
      )}

      <motion.div
        variants={container}
        initial="hidden"
        animate="show"
        className="grid md:grid-cols-3 gap-4"
      >
        <motion.div variants={item}>
          <Link to="/characters">
            <GlassCard className="cursor-pointer h-full group">
              <div className="flex items-start justify-between">
                <div className="text-4xl mb-3 group-hover:scale-110 transition-transform">
                  👥
                </div>
                <div className="w-8 h-8 rounded-full bg-sakura-100 flex items-center justify-center text-sakura-500 opacity-0 group-hover:opacity-100 transition-opacity">
                  →
                </div>
              </div>
              <h3 className="font-semibold text-sakura-600 text-lg">
                角色管理
              </h3>
              <p className="text-sm text-twilight-400 mt-1">
                查看角色状态、对话与记忆
              </p>
              <div className="mt-3 text-sakura-400 text-sm font-medium">
                查看详情 →
              </div>
            </GlassCard>
          </Link>
        </motion.div>

        <motion.div variants={item}>
          <Link to="/world">
            <GlassCard className="cursor-pointer h-full group">
              <div className="flex items-start justify-between">
                <div className="text-4xl mb-3 group-hover:scale-110 transition-transform">
                  🌍
                </div>
                <div className="w-8 h-8 rounded-full bg-sky-soft-100 flex items-center justify-center text-sky-soft-500 opacity-0 group-hover:opacity-100 transition-opacity">
                  →
                </div>
              </div>
              <h3 className="font-semibold text-sky-soft-500 text-lg">
                世界状态
              </h3>
              <p className="text-sm text-twilight-400 mt-1">
                虚拟时间、天气与事件
              </p>
              <div className="mt-3 text-sky-soft-400 text-sm font-medium">
                查看详情 →
              </div>
            </GlassCard>
          </Link>
        </motion.div>

        <motion.div variants={item}>
          <Link to="/map">
            <GlassCard className="cursor-pointer h-full group">
              <div className="flex items-start justify-between">
                <div className="text-4xl mb-3 group-hover:scale-110 transition-transform">
                  🗺️
                </div>
                <div className="w-8 h-8 rounded-full bg-twilight-100 flex items-center justify-center text-twilight-500 opacity-0 group-hover:opacity-100 transition-opacity">
                  →
                </div>
              </div>
              <h3 className="font-semibold text-twilight-500 text-lg">
                小镇地图
              </h3>
              <p className="text-sm text-twilight-400 mt-1">
                场景热力图与角色分布
              </p>
              <div className="mt-3 text-twilight-400 text-sm font-medium">
                查看详情 →
              </div>
            </GlassCard>
          </Link>
        </motion.div>
      </motion.div>
    </div>
  );
}
