import { createFileRoute } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { MessageCircle, User, Bot, Clock } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  StatusBadge,
} from "@/components/ui";
import { useOnebotMessages } from "@/lib/queries";

export const Route = createFileRoute("/qq-monitor")({
  component: QqMonitorPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.04 } },
};

const item = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0 },
};

// CQ 码清理正则：匹配 [CQ:xxx,data=...] 格式
const CQ_CODE_PATTERN = /\[CQ:[^\]]+\]/g;

function cleanCQCodes(text: string): string {
  if (!text) return "";
  return text.replace(CQ_CODE_PATTERN, "").trim();
}

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

function QqMonitorPage() {
  // 获取最近 100 条 QQ 消息，hook 内部已设置 10 秒自动刷新
  const { data, isLoading, error } = useOnebotMessages(100);

  const messages = data?.data ?? [];
  // 统计用户消息数与角色消息数
  const userCount = messages.filter((m) => m.sender === "user").length;
  const characterCount = messages.filter(
    (m) => m.sender === "character",
  ).length;

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="QQ 消息监控"
        subtitle="实时查看 OneBot 通道消息流（每 10 秒自动刷新）"
        icon="💬"
        backTo="/admin"
        backLabel="返回管理"
      />

      {/* 顶部统计栏 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          title="总消息数"
          value={data?.total ?? messages.length}
          icon="📊"
          color="twilight"
        />
        <StatCard title="用户消息" value={userCount} icon="👤" color="sky" />
        <StatCard
          title="角色消息"
          value={characterCount}
          icon="🤖"
          color="sakura"
        />
      </div>

      {/* 状态栏 */}
      <GlassCard hover={false}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2 text-twilight-500">
            <MessageCircle className="w-5 h-5 text-sakura-500" />
            <span className="font-semibold">消息流</span>
            <span className="text-xs text-twilight-400">
              （最近 {messages.length} 条）
            </span>
          </div>
          <StatusBadge status="ok" label="自动刷新中" />
        </div>
      </GlassCard>

      {isLoading && <LoadingSpinner text="正在加载 QQ 消息..." />}
      {error && <ErrorDisplay error={error} />}
      {data && messages.length === 0 && (
        <EmptyState
          icon="📭"
          title="暂无 QQ 消息"
          subtitle="当 OneBot 通道有消息流入时将显示在这里"
        />
      )}

      {/* 消息列表 */}
      {messages.length > 0 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="space-y-3"
        >
          {messages.map((msg) => {
            const isUser = msg.sender === "user";
            return (
              <motion.div key={msg.message_id} variants={item}>
                <div
                  className={`flex ${isUser ? "justify-start" : "justify-end"}`}
                >
                  <div
                    className={`flex items-start gap-3 max-w-[85%] ${
                      isUser ? "flex-row" : "flex-row-reverse"
                    }`}
                  >
                    {/* 头像 */}
                    <div
                      className={`w-10 h-10 rounded-2xl flex items-center justify-center shrink-0 shadow-md ${
                        isUser
                          ? "bg-gradient-to-br from-sky-soft-300 to-sky-soft-500 text-white"
                          : "bg-gradient-to-br from-sakura-300 to-sakura-500 text-white"
                      }`}
                    >
                      {isUser ? (
                        <User className="w-5 h-5" />
                      ) : (
                        <Bot className="w-5 h-5" />
                      )}
                    </div>
                    {/* 气泡 */}
                    <div
                      className={`px-4 py-3 rounded-2xl backdrop-blur-sm border shadow-sm ${
                        isUser
                          ? "bg-sky-soft-100/80 border-sky-soft-200/50 rounded-tl-sm text-twilight-700"
                          : "bg-sakura-100/80 border-sakura-200/50 rounded-tr-sm text-twilight-700"
                      }`}
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <span
                          className={`text-xs font-semibold ${
                            isUser ? "text-sky-soft-600" : "text-sakura-600"
                          }`}
                        >
                          {isUser ? "用户" : "角色"}
                        </span>
                        {msg.character_id && !isUser && (
                          <span className="text-xs text-twilight-400 px-1.5 py-0.5 rounded-lg bg-white/50">
                            {msg.character_id}
                          </span>
                        )}
                        {msg.user_id && isUser && (
                          <span className="text-xs text-twilight-400 px-1.5 py-0.5 rounded-lg bg-white/50">
                            {msg.user_id}
                          </span>
                        )}
                      </div>
                      <div className="text-sm break-words whitespace-pre-wrap">
                        {cleanCQCodes(msg.content) ||
                          "（空消息或含特殊格式码）"}
                      </div>
                      <div className="flex items-center gap-2 mt-2">
                        <Clock className="w-3 h-3 text-twilight-300" />
                        <span className="text-xs text-twilight-300">
                          {formatRelativeTime(msg.created_at)}
                        </span>
                        {msg.tokens != null && (
                          <span className="text-xs text-twilight-300">
                            · {msg.tokens} tokens
                          </span>
                        )}
                        {msg.cost != null && msg.cost > 0 && (
                          <span className="text-xs text-twilight-300">
                            · ${msg.cost.toFixed(4)}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </motion.div>
            );
          })}
        </motion.div>
      )}
    </div>
  );
}
