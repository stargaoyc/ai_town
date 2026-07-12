import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Clock, MessageCircle, User, Bot } from "lucide-react";
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
import { api } from "@/lib/api";

export const Route = createFileRoute("/conversations")({
  component: ConversationsPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.05 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

// 平台过滤选项
const platformFilters = [
  { key: "all", label: "全部" },
  { key: "qq", label: "QQ" },
  { key: "web", label: "Web" },
] as const;

type PlatformFilter = (typeof platformFilters)[number]["key"];

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

// 平台标签样式
function platformBadge(platform: string): {
  label: string;
  color: string;
  status: "ok" | "idle";
} {
  const p = platform.toLowerCase();
  if (p === "qq" || p === "onebot") {
    return {
      label: "QQ",
      color: "bg-sky-soft-100 text-sky-soft-600 border-sky-soft-200/50",
      status: "ok",
    };
  }
  if (p === "web") {
    return {
      label: "Web",
      color: "bg-sakura-100 text-sakura-600 border-sakura-200/50",
      status: "ok",
    };
  }
  return {
    label: platform,
    color: "bg-twilight-100 text-twilight-500 border-twilight-200/50",
    status: "idle",
  };
}

function ConversationsPage() {
  // 直接用 useQuery 获取所有会话
  const { data, isLoading, error } = useQuery({
    queryKey: ["conversations"],
    queryFn: api.getConversations,
  });
  const conversations = data?.data ?? [];

  // 平台过滤
  const [filter, setFilter] = useState<PlatformFilter>("all");

  // 过滤后的会话列表
  const filteredConversations = useMemo(() => {
    const sorted = [...conversations].sort(
      (a, b) =>
        new Date(b.last_message_at).getTime() -
        new Date(a.last_message_at).getTime(),
    );
    if (filter === "all") return sorted;
    if (filter === "qq") {
      return sorted.filter(
        (c) => c.platform.toLowerCase() === "qq" || c.platform.toLowerCase() === "onebot",
      );
    }
    // web
    return sorted.filter((c) => c.platform.toLowerCase() === "web");
  }, [conversations, filter]);

  // 统计：总会话数、QQ 会话数、Web 会话数
  const stats = useMemo(() => {
    const qq = conversations.filter(
      (c) => c.platform.toLowerCase() === "qq" || c.platform.toLowerCase() === "onebot",
    ).length;
    const web = conversations.filter(
      (c) => c.platform.toLowerCase() === "web",
    ).length;
    return { total: conversations.length, qq, web };
  }, [conversations]);

  return (
    <NavLayout>
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="会话管理"
          subtitle="查看所有角色与用户的对话会话"
          icon="🗨️"
        />

        {/* 顶部统计 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <StatCard
            title="总会话数"
            value={stats.total}
            icon="🗨️"
            color="sakura"
          />
          <StatCard title="QQ 会话" value={stats.qq} icon="💬" color="sky" />
          <StatCard
            title="Web 会话"
            value={stats.web}
            icon="🌐"
            color="twilight"
          />
        </div>

        {/* 平台过滤标签 */}
        <GlassCard hover={false}>
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-2">
              <span className="text-sm text-twilight-500 font-medium">
                平台筛选
              </span>
              <div className="flex gap-1">
                {platformFilters.map((f) => {
                  const active = filter === f.key;
                  return (
                    <motion.button
                      key={f.key}
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={() => setFilter(f.key)}
                      className={`px-3 py-1.5 rounded-xl text-sm font-medium border transition-all ${
                        active
                          ? "bg-gradient-to-r from-sakura-400 to-sakura-500 text-white border-transparent shadow-md shadow-sakura-400/30"
                          : "bg-white/60 text-twilight-600 border-sakura-200/50 hover:border-sakura-300/50"
                      }`}
                    >
                      {f.label}
                    </motion.button>
                  );
                })}
              </div>
            </div>
            <StatusBadge
              status="ok"
              label={`显示 ${filteredConversations.length} / ${conversations.length}`}
            />
          </div>
        </GlassCard>

        {isLoading && <LoadingSpinner text="正在加载会话..." />}
        {error && <ErrorDisplay error={error} />}
        {!isLoading && !error && conversations.length === 0 && (
          <EmptyState
            icon="🗨️"
            title="暂无会话"
            subtitle="当用户与角色开始对话后，会话将显示在这里"
          />
        )}

        {/* 过滤后无结果 */}
        {!isLoading &&
          !error &&
          conversations.length > 0 &&
          filteredConversations.length === 0 && (
            <EmptyState
              icon="🔍"
              title="未匹配到会话"
              subtitle="尝试切换平台筛选条件"
            />
          )}

        {/* 会话列表 */}
        {filteredConversations.length > 0 && (
          <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="grid md:grid-cols-2 gap-4"
          >
            {filteredConversations.map((conv) => {
              const pb = platformBadge(conv.platform);
              return (
                <motion.div key={conv.id} variants={item}>
                  <GlassCard className="space-y-3" hover>
                    {/* 顶部：平台标签 + 会话 ID */}
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium border ${pb.color}`}
                      >
                        {pb.label}
                      </span>
                      <span className="text-xs text-twilight-300 font-mono truncate max-w-[60%]">
                        {conv.id}
                      </span>
                    </div>

                    {/* 角色与用户信息 */}
                    <div className="grid grid-cols-2 gap-3">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0 bg-gradient-to-br from-sakura-300 to-sakura-500 text-white">
                          <Bot className="w-4 h-4" />
                        </div>
                        <div className="min-w-0">
                          <div className="text-xs text-twilight-400">角色</div>
                          <div className="text-sm font-medium text-twilight-600 truncate">
                            {conv.character_id}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0 bg-gradient-to-br from-sky-soft-300 to-sky-soft-500 text-white">
                          <User className="w-4 h-4" />
                        </div>
                        <div className="min-w-0">
                          <div className="text-xs text-twilight-400">用户</div>
                          <div className="text-sm font-medium text-twilight-600 truncate">
                            {conv.user_id}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* 最后消息时间 */}
                    <div className="flex items-center gap-2 pt-1 border-t border-white/40">
                      <MessageCircle className="w-3.5 h-3.5 text-sakura-400" />
                      <Clock className="w-3.5 h-3.5 text-twilight-300" />
                      <span className="text-xs text-twilight-400">
                        最后消息：{formatRelativeTime(conv.last_message_at)}
                      </span>
                    </div>
                  </GlassCard>
                </motion.div>
              );
            })}
          </motion.div>
        )}
      </div>
    </NavLayout>
  );
}
