import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { motion } from "framer-motion";
import {
  Bell,
  Share2,
  AlertTriangle,
  HeartCrack,
  MessageCircle,
  Trash2,
  CheckCheck,
  Clock,
  RefreshCw,
} from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  EmptyState,
  AnimeButton,
  StatusBadge,
  LoadingSpinner,
  ErrorDisplay,
  ConfirmDialog,
} from "@/components/ui";
import { toast } from "@/stores/toast";
import {
  useNotifications,
  useMarkNotificationRead,
  useMarkAllNotificationsRead,
  useDeleteNotification,
  useClearAllNotifications,
} from "@/lib/queries";

export const Route = createFileRoute("/notifications")({
  component: NotificationsPage,
});

// 通知类型
type NotificationType = "share" | "system" | "character" | "qq";

// 通知类型配置：图标、颜色、标签
const typeConfig: Record<
  NotificationType,
  {
    label: string;
    icon: typeof Bell;
    color: string;
    iconBg: string;
    badge: { status: "ok" | "warning" | "error" | "idle"; label: string };
  }
> = {
  share: {
    label: "主动分享",
    icon: Share2,
    color: "text-sakura-600",
    iconBg: "bg-gradient-to-br from-sakura-300 to-sakura-500",
    badge: { status: "ok", label: "分享" },
  },
  system: {
    label: "系统告警",
    icon: AlertTriangle,
    color: "text-amber-600",
    iconBg: "bg-gradient-to-br from-amber-300 to-amber-500",
    badge: { status: "warning", label: "告警" },
  },
  character: {
    label: "角色状态异常",
    icon: HeartCrack,
    color: "text-red-500",
    iconBg: "bg-gradient-to-br from-red-300 to-red-500",
    badge: { status: "error", label: "异常" },
  },
  qq: {
    label: "QQ 连接状态",
    icon: MessageCircle,
    color: "text-sky-soft-600",
    iconBg: "bg-gradient-to-br from-sky-soft-300 to-sky-soft-500",
    badge: { status: "idle", label: "QQ" },
  },
};

// 格式化为相对时间
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

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0 },
};

function NotificationsPage() {
  const { data, isLoading, error, refetch, isFetching } = useNotifications(100); // 无轮询：WS 推送失效缓存，此处可手动刷新
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  const deleteNotif = useDeleteNotification();
  const clearAll = useClearAllNotifications();

  const notifications = data?.data ?? [];
  const unreadCount = data?.unread ?? 0;
  const totalCount = data?.total ?? 0;
  const byType = (type: NotificationType) => notifications.filter((n) => n.type === type).length;

  const [confirmClearAll, setConfirmClearAll] = useState(false);

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="通知中心"
        subtitle="查看主动分享、系统告警、角色状态与 QQ 连接通知"
        icon="🔔"
      />

      {/* 顶部统计卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard title="通知总数" value={totalCount} icon="🔔" color="sakura" />
        <StatCard title="未读通知" value={unreadCount} icon="📬" color="twilight" />
        <StatCard title="系统告警" value={byType("system")} icon="⚠️" color="sky" />
        <StatCard title="角色异常" value={byType("character")} icon="💔" color="sakura" />
      </div>

      {/* 操作工具栏 */}
      <GlassCard hover={false}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2">
            <Bell className="w-5 h-5 text-sakura-500" />
            <span className="font-semibold text-twilight-500">通知列表</span>
            {unreadCount > 0 && <StatusBadge status="warning" label={`${unreadCount} 条未读`} />}
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              aria-label="刷新通知"
              title="刷新"
              className="ml-1 p-1 rounded-lg text-twilight-400 hover:bg-sakura-100/60 hover:text-sakura-600 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />
            </button>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <AnimeButton
              onClick={() => markAllRead.mutate()}
              variant="secondary"
              className="!px-3 !py-2 !text-sm"
              disabled={unreadCount === 0 || markAllRead.isPending}
            >
              <span className="flex items-center gap-1.5">
                <CheckCheck className="w-3.5 h-3.5" />
                全部已读
              </span>
            </AnimeButton>
            <AnimeButton
              onClick={() => setConfirmClearAll(true)}
              variant="danger"
              className="!px-3 !py-2 !text-sm"
              disabled={totalCount === 0 || clearAll.isPending}
            >
              <span className="flex items-center gap-1.5">
                <Trash2 className="w-3.5 h-3.5" />
                清除全部
              </span>
            </AnimeButton>
          </div>
        </div>
      </GlassCard>

      {/* 加载状态 */}
      {isLoading && <LoadingSpinner text="正在加载通知..." />}
      {error && <ErrorDisplay error={error} />}

      {/* 空状态 */}
      {!isLoading && !error && totalCount === 0 && (
        <EmptyState
          icon="🔔"
          title="暂无通知"
          subtitle="角色主动分享、系统告警等事件会自动出现在这里。可点击「模拟通知」按钮测试"
        />
      )}

      {/* 通知列表 */}
      {totalCount > 0 && (
        <motion.div variants={container} initial="hidden" animate="show" className="space-y-3">
          {notifications.map((notif) => {
            const cfg = typeConfig[notif.type as NotificationType] ?? typeConfig.system;
            const Icon = cfg.icon;
            return (
              <motion.div key={notif.id} variants={item}>
                <GlassCard
                  hover
                  className={`space-y-2 ${!notif.read ? "ring-2 ring-sakura-300/40" : ""}`}
                >
                  <div className="flex items-start gap-3">
                    {/* 类型图标 */}
                    <div
                      className={`w-10 h-10 rounded-2xl ${cfg.iconBg} flex items-center justify-center text-white shrink-0 shadow-md`}
                    >
                      <Icon className="w-5 h-5" />
                    </div>

                    {/* 通知内容 */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h4 className={`font-semibold ${cfg.color} text-sm md:text-base`}>
                          {notif.title}
                        </h4>
                        <StatusBadge status={cfg.badge.status} label={cfg.badge.label} />
                        {!notif.read && (
                          <span className="w-2 h-2 rounded-full bg-sakura-500 animate-pulse" />
                        )}
                      </div>
                      <p className="text-sm text-twilight-500 mt-1 leading-relaxed">
                        {notif.content}
                      </p>
                      <div className="flex items-center gap-1 mt-2 text-xs text-twilight-300">
                        <Clock className="w-3 h-3" />
                        <span>{formatRelativeTime(notif.created_at)}</span>
                      </div>
                    </div>

                    {/* 操作按钮 */}
                    <div className="flex flex-col gap-1.5 shrink-0">
                      {!notif.read && (
                        <motion.button
                          whileHover={{ scale: 1.1 }}
                          whileTap={{ scale: 0.9 }}
                          onClick={() =>
                            markRead.mutate(notif.id, {
                              onError: () => toast.error("标记已读失败"),
                            })
                          }
                          aria-label="标记已读"
                          title="标记已读"
                          className="w-8 h-8 rounded-xl flex items-center justify-center text-twilight-400 hover:bg-sakura-100/60 hover:text-sakura-600 transition-colors"
                        >
                          <CheckCheck className="w-4 h-4" />
                        </motion.button>
                      )}
                      <motion.button
                        whileHover={{ scale: 1.1 }}
                        whileTap={{ scale: 0.9 }}
                        onClick={() =>
                          deleteNotif.mutate(notif.id, {
                            onError: () => toast.error("删除通知失败"),
                          })
                        }
                        aria-label="删除通知"
                        title="删除通知"
                        className="w-8 h-8 rounded-xl flex items-center justify-center text-twilight-400 hover:bg-red-50 hover:text-red-500 transition-colors"
                      >
                        <Trash2 className="w-4 h-4" />
                      </motion.button>
                    </div>
                  </div>
                </GlassCard>
              </motion.div>
            );
          })}
        </motion.div>
      )}

      <ConfirmDialog
        open={confirmClearAll}
        title="清除全部通知"
        description={`将删除全部 ${totalCount} 条通知（含未读），此操作不可恢复。`}
        confirmText="全部清除"
        danger
        onConfirm={() => {
          setConfirmClearAll(false);
          clearAll.mutate(undefined, {
            onError: () => toast.error("清除通知失败"),
          });
        }}
        onCancel={() => setConfirmClearAll(false)}
      />
    </div>
  );
}
