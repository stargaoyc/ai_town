import { createFileRoute, Link } from "@tanstack/react-router";
import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Send, RotateCw, MessageCircle, Brain } from "lucide-react";
import {
  GlassCard,
  LoadingSpinner,
  ErrorDisplay,
  ProgressBar,
  StatCard,
  EmptyState,
  AnimeButton,
  AnimeInput,
} from "@/components/ui";
import {
  useCharacter,
  useMemories,
  useMessages,
  useSendMessage,
} from "@/lib/queries";
import type { Message } from "@/lib/api";

export const Route = createFileRoute("/characters/$characterId")({
  component: CharacterDetailPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.08 } },
};

const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0 },
};

// CQ 码清理正则：匹配 [CQ:xxx,data=...] 格式
const CQ_CODE_PATTERN = /\[CQ:[^\]]+\]/g;

function cleanCQCodes(text: string): string {
  if (!text) return "";
  return text.replace(CQ_CODE_PATTERN, "").trim();
}

function CharacterDetailPage() {
  const { characterId } = Route.useParams();
  const { data: character, isLoading, error } = useCharacter(characterId);
  const { data: memoriesData } = useMemories(characterId);
  const { data: messagesData } = useMessages(characterId);
  const sendMessage = useSendMessage();
  const [input, setInput] = useState("");
  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 合并服务端消息和乐观更新的消息，按时间排序
  const allMessages = [
    ...(messagesData?.data ?? []),
    ...optimisticMessages,
  ].sort(
    (a, b) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [allMessages.length]);

  const handleSend = () => {
    if (!input.trim()) return;
    const content = input.trim();

    // 乐观更新：立即显示用户消息
    const optimisticMsg: Message = {
      id: `temp-${Date.now()}`,
      conversation_id: "",
      sender: "user",
      content,
      created_at: new Date().toISOString(),
    };
    setOptimisticMessages((prev) => [...prev, optimisticMsg]);

    sendMessage.mutate(
      { characterId, userId: "web_user", content },
      {
        onSuccess: () => {
          // 移除乐观消息，服务端会通过 query invalidation 返回完整列表（含角色回复）
          // 不再乐观添加回复，避免与 query 刷新后的数据重复
          setOptimisticMessages((prev) =>
            prev.filter((m) => m.id !== optimisticMsg.id),
          );
        },
        onError: () => {
          // 发送失败也移除乐观消息
          setOptimisticMessages((prev) =>
            prev.filter((m) => m.id !== optimisticMsg.id),
          );
        },
      },
    );
    setInput("");
  };

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorDisplay error={error} />;
  if (!character) return <EmptyState title="角色不存在" />;

  const state = character.state
    ? {
        location: character.state.location,
        stamina: character.state.stamina ?? 0,
        satiety: character.state.satiety ?? 0,
        mood: character.state.mood,
        money: character.state.money ?? 0,
        phone_battery: character.state.phone_battery ?? 0,
        social_energy: character.state.social_energy ?? 0,
        current_action: character.state.current_action,
        version: character.state.version ?? 0,
      }
    : undefined;

  return (
    <motion.div
      variants={container}
      initial="hidden"
      animate="show"
      className="space-y-6"
    >
      <motion.div variants={item}>
        <Link
          to="/characters"
          className="inline-flex items-center gap-1.5 text-sm text-twilight-400 hover:text-sakura-600 transition-colors px-3 py-1.5 rounded-xl bg-white/40 hover:bg-white/60 w-fit"
        >
          <ArrowLeft className="w-4 h-4" />
          返回列表
        </Link>
      </motion.div>

      <motion.div variants={item}>
        <GlassCard>
          <div className="flex items-center gap-4">
            <motion.div
              className="w-20 h-20 rounded-2xl bg-gradient-to-br from-sakura-300 via-sakura-400 to-twilight-300 flex items-center justify-center text-white font-bold text-3xl shadow-lg"
              whileHover={{ rotate: 5, scale: 1.05 }}
            >
              {character.name[0]}
            </motion.div>
            <div className="flex-1 min-w-0">
              <h1 className="text-2xl font-bold text-sakura-600">
                {character.name}
              </h1>
              <p className="text-twilight-400 mt-1">
                {character.age ? `${character.age}岁 · ` : ""}
                {character.occupation ?? "未知职业"}
              </p>
              {character.backstory && (
                <p className="text-sm text-twilight-300 mt-2 line-clamp-2">
                  {character.backstory}
                </p>
              )}
            </div>
          </div>
        </GlassCard>
      </motion.div>

      {state && (
        <motion.div variants={item}>
          <GlassCard>
            <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
              <span>📊</span> 实时状态
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <StatCard
                title="位置"
                value={state.location ?? "未知"}
                icon="📍"
              />
              <StatCard
                title="情绪"
                value={state.mood ?? "calm"}
                icon="😊"
                color="twilight"
              />
              <StatCard
                title="金钱"
                value={`¥${state.money}`}
                icon="💰"
                color="sky"
              />
              <StatCard
                title="当前行为"
                value={
                  state.current_action?.action_name ??
                  state.current_action?.action_id ??
                  "无"
                }
                icon="🎯"
                color="sakura"
              />
            </div>
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="text-twilight-400 flex items-center gap-1.5">
                    <RotateCw className="w-3.5 h-3.5" /> 体力
                  </span>
                  <span className="text-sakura-600 font-medium">
                    {state.stamina}/100
                  </span>
                </div>
                <ProgressBar value={state.stamina} color="sakura" />
              </div>
              <div>
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="text-twilight-400">🍽️ 饱腹度</span>
                  <span className="text-sky-soft-500 font-medium">
                    {state.satiety}/100
                  </span>
                </div>
                <ProgressBar value={state.satiety} color="sky" />
              </div>
              <div>
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="text-twilight-400">💬 社交能量</span>
                  <span className="text-twilight-500 font-medium">
                    {state.social_energy}/100
                  </span>
                </div>
                <ProgressBar value={state.social_energy} color="twilight" />
              </div>
              <div>
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="text-twilight-400">📱 手机电量</span>
                  <span className="text-sakura-600 font-medium">
                    {state.phone_battery}%
                  </span>
                </div>
                <ProgressBar value={state.phone_battery} color="sakura" />
              </div>
            </div>
          </GlassCard>
        </motion.div>
      )}

      <motion.div variants={item}>
        <GlassCard>
          <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
            <MessageCircle className="w-5 h-5" /> 对话
          </h3>
          <div className="space-y-2 mb-4 max-h-64 overflow-y-auto pr-2">
            {allMessages.length === 0 && (
              <EmptyState
                icon="💌"
                title="暂无消息"
                subtitle="发送第一条消息开始对话吧"
              />
            )}
            {allMessages.map((msg) => {
              const displayContent = cleanCQCodes(msg.content);
              if (!displayContent) return null;
              return (
                <motion.div
                  key={msg.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`p-3 rounded-2xl text-sm ${
                    msg.sender === "user"
                      ? "bg-gradient-to-r from-sakura-100 to-sakura-200/50 text-sakura-700 ml-8 rounded-tr-sm"
                      : msg.sender === "character"
                        ? "bg-gradient-to-r from-sky-soft-100 to-sky-soft-200/50 text-sky-soft-600 mr-8 rounded-tl-sm"
                        : "bg-white/50 text-twilight-500"
                  }`}
                >
                  {displayContent}
                </motion.div>
              );
            })}
            <div ref={messagesEndRef} />
          </div>
          <div className="flex gap-2">
            <AnimeInput
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              placeholder="输入消息..."
              className="flex-1 text-sm"
            />
            <AnimeButton
              onClick={handleSend}
              disabled={sendMessage.isPending || !input.trim()}
              className="px-4"
            >
              <Send className="w-4 h-4" />
            </AnimeButton>
          </div>
        </GlassCard>
      </motion.div>

      <motion.div variants={item}>
        <GlassCard>
          <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2 text-lg">
            <Brain className="w-5 h-5" /> 最近记忆
          </h3>
          <div className="space-y-3">
            {memoriesData?.data?.length === 0 && (
              <EmptyState icon="💭" title="暂无记忆" />
            )}
            {memoriesData?.data?.map((mem) => (
              <motion.div
                key={mem.id}
                whileHover={{ scale: 1.01 }}
                className="p-3 rounded-xl bg-white/30 border border-white/20 hover:bg-white/50 transition-colors"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-twilight-400">
                    {new Date(mem.timestamp).toLocaleString("zh-CN")}
                  </span>
                  <span className="text-xs text-sakura-500 font-semibold">
                    ⭐ {mem.importance}
                  </span>
                </div>
                <p className="text-sm text-twilight-500">{mem.content}</p>
              </motion.div>
            )) ?? <p className="text-sm text-twilight-400">加载中...</p>}
          </div>
        </GlassCard>
      </motion.div>
    </motion.div>
  );
}
