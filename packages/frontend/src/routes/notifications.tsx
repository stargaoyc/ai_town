import { createFileRoute } from "@tanstack/react-router";
import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  Bell,
  Share2,
  AlertTriangle,
  HeartCrack,
  MessageCircle,
  Trash2,
  CheckCheck,
  Sparkles,
  Clock,
} from "lucide-react";
import {
  NavLayout,
  GlassCard,
  PageHeader,
  StatCard,
  EmptyState,
  AnimeButton,
  StatusBadge,
} from "@/components/ui";

export const Route = createFileRoute("/notifications")({
  component: NotificationsPage,
});

// 通知类型
type NotificationType = "share" | "system" | "character" | "qq";

// 通知数据结构
interface AppNotification {
  id: string;
  type: NotificationType;
  title: string;
  content: string;
  created_at: string;
  read: boolean;
}

// localStorage 存储键
const STORAGE_KEY = "aitown_notifications";

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

// 模拟通知模板（用于测试）
const mockTemplates: Omit<AppNotification, "id" | "created_at" | "read">[] = [
  {
    type: "share",
    title: "角色主动分享了动态",
    content: "樱花酱主动向你分享了一张图书馆窗外的夕阳照片，并附言「今天的天空好美呀~」",
  },
  {
    type: "system",
    title: "World Engine 告警",
    content: "世界引擎 Tick 延迟超过 5 秒，当前延迟 8.2s，请检查服务器负载",
  },
  {
    type: "character",
    title: "角色状态异常",
    content: "角色「樱花酱」的精力值已降至 12，饱腹值降至 8，请关注其状态",
  },
  {
    type: "qq",
    title: "QQ 连接已恢复",
    content: "OneBot 反向 WebSocket 已重新连接，消息收发恢复正常",
  },
  {
    type: "system",
    title: "LLM 调用超时",
    content: "LLM 接口响应时间过长，已自动重试 3 次，部分角色回复可能延迟",
  },
  {
    type: "character",
    title: "角色情绪低落",
    content: "角色「月野兔」连续 3 个 Tick 情绪为「沮丧」，建议触发社交互动",
  },
];

// 从 localStorage 读取通知
function loadNotifications(): AppNotification[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as AppNotification[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// 保存通知到 localStorage
function saveNotifications(notifications: AppNotification[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(notifications));
  } catch {
    // 存储失败时静默忽略
  }
}

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
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [mounted, setMounted] = useState(false);

  // 初始化：从 localStorage 加载
  useEffect(() => {
    setNotifications(loadNotifications());
    setMounted(true);
  }, []);

  // 通知变更时同步到 localStorage
  useEffect(() => {
    if (mounted) {
      saveNotifications(notifications);
    }
  }, [notifications, mounted]);

  // 添加模拟通知（用于测试）
  const handleAddMock = useCallback(() => {
    const template =
      mockTemplates[Math.floor(Math.random() * mockTemplates.length)];
    const newNotif: AppNotification = {
      ...template,
      id: `notif-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      created_at: new Date().toISOString(),
      read: false,
    };
    setNotifications((prev) => [newNotif, ...prev]);
  }, []);

  // 清除全部通知
  const handleClearAll = useCallback(() => {
    setNotifications([]);
  }, []);

  // 标记单条为已读
  const handleMarkRead = useCallback((id: string) => {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n)),
    );
  }, []);

  // 标记全部为已读
  const handleMarkAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  // 删除单条通知
  const handleDelete = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  // 统计
  const unreadCount = notifications.filter((n) => !n.read).length;
  const totalCount = notifications.length;
  const byType = (type: NotificationType) =>
    notifications.filter((n) => n.type === type).length;

  return (
    <NavLayout>
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="通知中心"
          subtitle="查看主动分享、系统告警、角色状态与 QQ 连接通知"
          icon="🔔"
        />

        {/* 顶部统计卡片 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            title="通知总数"
            value={totalCount}
            icon="🔔"
            color="sakura"
          />
          <StatCard
            title="未读通知"
            value={unreadCount}
            icon="📬"
            color="twilight"
          />
          <StatCard
            title="系统告警"
            value={byType("system")}
            icon="⚠️"
            color="sky"
          />
          <StatCard
            title="角色异常"
            value={byType("character")}
            icon="💔"
            color="sakura"
          />
        </div>

        {/* 操作工具栏 */}
        <GlassCard hover={false}>
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-2">
              <Bell className="w-5 h-5 text-sakura-500" />
              <span className="font-semibold text-twilight-500">通知列表</span>
              {unreadCount > 0 && (
                <StatusBadge status="warning" label={`${unreadCount} 条未读`} />
              )}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <AnimeButton
                onClick={handleAddMock}
                variant="secondary"
                className="!px-3 !py-2 !text-sm"
              >
                <span className="flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5" />
                  模拟通知
                </span>
              </AnimeButton>
              <AnimeButton
                onClick={handleMarkAllRead}
                variant="secondary"
                className="!px-3 !py-2 !text-sm"
                disabled={unreadCount === 0}
              >
                <span className="flex items-center gap-1.5">
                  <CheckCheck className="w-3.5 h-3.5" />
                  全部已读
                </span>
              </AnimeButton>
              <AnimeButton
                onClick={handleClearAll}
                variant="danger"
                className="!px-3 !py-2 !text-sm"
                disabled={totalCount === 0}
              >
                <span className="flex items-center gap-1.5">
                  <Trash2 className="w-3.5 h-3.5" />
                  清除全部
                </span>
              </AnimeButton>
            </div>
          </div>
        </GlassCard>

        {/* 空状态 */}
        {totalCount === 0 && (
          <EmptyState
            icon="🔔"
            title="暂无通知"
            subtitle="后端暂无专门的通知 API，可点击「模拟通知」按钮生成测试通知"
          />
        )}

        {/* 通知列表 */}
        {totalCount > 0 && (
          <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="space-y-3"
          >
            {notifications.map((notif) => {
              const cfg = typeConfig[notif.type];
              const Icon = cfg.icon;
              return (
                <motion.div key={notif.id} variants={item}>
                  <GlassCard
                    hover
                    className={`space-y-2 ${
                      !notif.read ? "ring-2 ring-sakura-300/40" : ""
                    }`}
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
                          <h4
                            className={`font-semibold ${cfg.color} text-sm md:text-base`}
                          >
                            {notif.title}
                          </h4>
                          <StatusBadge
                            status={cfg.badge.status}
                            label={cfg.badge.label}
                          />
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
                            onClick={() => handleMarkRead(notif.id)}
                            title="标记已读"
                            className="w-8 h-8 rounded-xl flex items-center justify-center text-twilight-400 hover:bg-sakura-100/60 hover:text-sakura-600 transition-colors"
                          >
                            <CheckCheck className="w-4 h-4" />
                          </motion.button>
                        )}
                        <motion.button
                          whileHover={{ scale: 1.1 }}
                          whileTap={{ scale: 0.9 }}
                          onClick={() => handleDelete(notif.id)}
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
      </div>
    </NavLayout>
  );
}
