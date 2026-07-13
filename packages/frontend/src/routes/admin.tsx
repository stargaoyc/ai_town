import { createFileRoute } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { Link } from "@tanstack/react-router";
import { Settings, Zap, CheckCircle2, XCircle, LayoutGrid } from "lucide-react";
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

// 功能导航项（从原"更多"下拉菜单迁移）
const toolLinks = [
  { to: "/import", label: "角色导入", icon: "📥", desc: "导入 YAML 角色卡" },
  {
    to: "/state-charts",
    label: "状态图表",
    icon: "📊",
    desc: "角色状态趋势可视化",
  },
  {
    to: "/memories",
    label: "记忆时间线",
    icon: "🧠",
    desc: "查看角色记忆片段",
  },
  { to: "/diaries", label: "角色日记", icon: "📖", desc: "基于记忆的叙事归档" },
  {
    to: "/person-memory",
    label: "用户记忆",
    icon: "👤",
    desc: "角色对用户的记忆",
  },
  { to: "/reflections", label: "反思查看", icon: "💭", desc: "角色反思与洞察" },
  { to: "/plans", label: "规划系统", icon: "📋", desc: "角色计划与进度" },
  {
    to: "/relationships",
    label: "关系图谱",
    icon: "🔗",
    desc: "角色社交关系网络",
  },
  { to: "/metrics", label: "指标面板", icon: "📈", desc: "Prometheus 指标" },
  { to: "/monitoring", label: "系统监控", icon: "📡", desc: "指标与日志监控" },
  { to: "/cost", label: "成本仪表", icon: "💰", desc: "LLM 调用成本分析" },
  { to: "/events", label: "事件时间线", icon: "⏱️", desc: "世界事件流" },
  { to: "/actions", label: "行为日志", icon: "📝", desc: "角色行为记录" },
  { to: "/qq-monitor", label: "QQ监控", icon: "💬", desc: "QQ 消息监控" },
  { to: "/shares", label: "主动分享", icon: "📤", desc: "角色主动分享历史" },
  { to: "/export", label: "导出", icon: "📦", desc: "聊天记录导出" },
  { to: "/conversations", label: "会话管理", icon: "🗨️", desc: "管理对话会话" },
  { to: "/vector-search", label: "向量检索", icon: "🔍", desc: "语义搜索测试" },
  { to: "/snapshots", label: "快照管理", icon: "📸", desc: "世界快照管理" },
  { to: "/character-card", label: "角色卡", icon: "🎴", desc: "角色卡预览" },
  { to: "/compare", label: "角色对比", icon: "🔄", desc: "多角色对比分析" },
];

function AdminPage() {
  const { data: status, isLoading, error } = useAdminStatus();
  const forceTick = useForceTick();

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="系统管理"
        subtitle="运维操作、状态监控与功能导航"
        icon="⚙️"
      />

      {/* 功能导航区 */}
      <motion.div variants={container} initial="hidden" animate="show">
        <GlassCard hover={false}>
          <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
            <LayoutGrid className="w-5 h-5" />
            功能导航
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {toolLinks.map((tl) => (
              <motion.div key={tl.to} variants={item}>
                <Link
                  to={tl.to}
                  className="block p-4 rounded-2xl bg-white/40 border border-white/40 hover:border-sakura-200/60 hover:bg-sakura-50/40 transition-all hover:scale-[1.02] group"
                >
                  <div className="flex items-start gap-3">
                    <span className="text-2xl shrink-0">{tl.icon}</span>
                    <div className="min-w-0">
                      <div className="font-medium text-twilight-600 text-sm group-hover:text-sakura-600 transition-colors">
                        {tl.label}
                      </div>
                      <div className="text-xs text-twilight-400 mt-0.5 truncate">
                        {tl.desc}
                      </div>
                    </div>
                  </div>
                </Link>
              </motion.div>
            ))}
          </div>
        </GlassCard>
      </motion.div>

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
