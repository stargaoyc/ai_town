import { createFileRoute } from "@tanstack/react-router";
import { useMemo } from "react";
import { motion } from "framer-motion";
import { Clock, Bot, Coins, Hash } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  StatusBadge,
} from "@/components/ui";
import { useProactiveShares } from "@/lib/queries";

export const Route = createFileRoute("/shares")({
  component: SharesPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.05 } },
};

const item = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0 },
};

// 将时间戳格式化为相对时间（如"3分钟前"）
function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (seconds < 60) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (hours < 24) return `${hours} 小时前`;
  if (days < 7) return `${days} 天前`;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function SharesPage() {
  // 获取最近 100 条主动分享
  const { data, isLoading, error } = useProactiveShares(100);
  const shares = data?.data ?? [];

  // 统计：总 token 消耗与总成本
  const stats = useMemo(() => {
    const totalTokens = shares.reduce((sum, s) => sum + (s.tokens ?? 0), 0);
    const totalCost = shares.reduce((sum, s) => sum + (s.cost ?? 0), 0);
    return { totalTokens, totalCost };
  }, [shares]);

  return (
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="主动分享历史"
          subtitle="角色在 Tick 中自主决定分享的内容记录"
          icon="📤"
        />

        {/* 顶部统计 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <StatCard
            title="总分享数"
            value={data?.total ?? shares.length}
            icon="📤"
            color="sakura"
          />
          <StatCard
            title="总 Token 消耗"
            value={stats.totalTokens.toLocaleString()}
            icon="🔢"
            color="sky"
          />
          <StatCard
            title="总成本"
            value={`$${stats.totalCost.toFixed(4)}`}
            icon="💰"
            color="twilight"
          />
        </div>

        {/* 状态栏 */}
        <GlassCard hover={false}>
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-2 text-twilight-500">
              <span className="font-semibold">分享流</span>
              <span className="text-xs text-twilight-400">
                （最近 {shares.length} 条）
              </span>
            </div>
            <StatusBadge
              status={shares.length > 0 ? "ok" : "idle"}
              label={shares.length > 0 ? "有分享记录" : "暂无分享"}
            />
          </div>
        </GlassCard>

        {isLoading && <LoadingSpinner text="正在加载分享历史..." />}
        {error && <ErrorDisplay error={error} />}
        {!isLoading && !error && shares.length === 0 && (
          <EmptyState
            icon="📤"
            title="暂无主动分享"
            subtitle="角色还没有主动分享过内容，角色会在 Tick 中自主决定是否分享"
          />
        )}

        {/* 分享列表 - 聊天气泡样式（角色头像 + 粉色气泡） */}
        {shares.length > 0 && (
          <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="space-y-4"
          >
            {shares.map((share) => (
              <motion.div key={share.message_id} variants={item}>
                <div className="flex justify-end">
                  <div className="flex items-start gap-3 max-w-[85%] flex-row-reverse">
                    {/* 角色头像 */}
                    <div className="w-10 h-10 rounded-2xl flex items-center justify-center shrink-0 shadow-md bg-gradient-to-br from-sakura-300 to-sakura-500 text-white">
                      <Bot className="w-5 h-5" />
                    </div>
                    {/* 粉色气泡 */}
                    <div className="px-4 py-3 rounded-2xl backdrop-blur-sm border shadow-sm bg-sakura-100/80 border-sakura-200/50 rounded-tr-sm text-twilight-700">
                      {/* 头部信息 */}
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs font-semibold text-sakura-600">
                          角色分享
                        </span>
                        {share.conversation_id && (
                          <span className="text-xs text-twilight-400 px-1.5 py-0.5 rounded-lg bg-white/50">
                            {share.conversation_id}
                          </span>
                        )}
                      </div>
                      {/* 分享内容 */}
                      <div className="text-sm break-words whitespace-pre-wrap">
                        {share.content}
                      </div>
                      {/* 底部元信息：时间 / token / 成本 */}
                      <div className="flex items-center gap-3 mt-2 flex-wrap">
                        <span className="text-xs text-twilight-300 flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {formatRelativeTime(share.created_at)}
                        </span>
                        {share.tokens != null && (
                          <span className="text-xs text-twilight-300 flex items-center gap-1">
                            <Hash className="w-3 h-3" />
                            {share.tokens} tokens
                          </span>
                        )}
                        {share.cost != null && share.cost > 0 && (
                          <span className="text-xs text-twilight-300 flex items-center gap-1">
                            <Coins className="w-3 h-3" />${share.cost.toFixed(4)}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </motion.div>
            ))}
          </motion.div>
        )}
      </div>
  );
}
