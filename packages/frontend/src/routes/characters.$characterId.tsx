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

function CharacterDetailPage() {
  const { characterId } = Route.useParams();
  const { data: character, isLoading, error } = useCharacter(characterId);
  const { data: memoriesData } = useMemories(characterId);
  const { data: messagesData } = useMessages(characterId);
  const sendMessage = useSendMessage();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messagesData?.data?.length]);

  const handleSend = () => {
    if (!input.trim()) return;
    sendMessage.mutate({ characterId, userId: "web_user", content: input });
    setInput("");
  };

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorDisplay error={error} />;
  if (!character) return <EmptyState title="角色不存在" />;

  const state = character.state;

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
              <StatCard title="版本" value={`v${state.version}`} icon="🔄" />
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
            {messagesData?.data?.length === 0 && (
              <EmptyState
                icon="💌"
                title="暂无消息"
                subtitle="发送第一条消息开始对话吧"
              />
            )}
            {messagesData?.data?.map((msg) => (
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
                {msg.content}
              </motion.div>
            )) ?? <p className="text-sm text-twilight-400">加载中...</p>}
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
