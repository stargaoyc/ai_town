import { createFileRoute, Link } from "@tanstack/react-router";
import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { Send, Bot, User, ArrowLeft, MessageCircle } from "lucide-react";
import { GlassCard, LoadingSpinner } from "@/components/ui";
import { useMessages, useSendMessage } from "@/lib/queries";
import { useAuthStore } from "@/stores/auth";
import { useChatSocket } from "@/hooks/useChatSocket";

export const Route = createFileRoute("/chat/$characterId")({
  component: ChatPage,
  loader: ({ params }) => ({ characterId: params.characterId }),
});

function ChatPage() {
  const { characterId } = Route.useParams();
  const userId = useAuthStore((s) => s.userId);
  const { data: messages, isLoading } = useMessages(characterId);
  const sendMutation = useSendMessage();
  const { status: wsStatus, send: wsSend } = useChatSocket(characterId);
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || !userId) return;
    // 优先走 WebSocket（实时），回退到 REST
    const sent = wsSend(text);
    if (!sent) {
      sendMutation.mutate({ characterId, userId, content: text });
    }
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // 按会话分组显示消息
  const allMessages = (messages?.data ?? []).slice().reverse();

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)] animate-fade-in-up">
      {/* 顶部栏 */}
      <div className="flex items-center gap-3 mb-4">
        <Link to="/characters" className="p-2 rounded-xl hover:bg-white/40 transition-colors">
          <ArrowLeft className="w-5 h-5 text-twilight-500" />
        </Link>
        <div className="flex-1">
          <h1 className="text-lg font-semibold text-twilight-600">与角色对话</h1>
          <p className="text-xs text-twilight-400">
            {wsStatus === "open" ? "已连接" : wsStatus === "connecting" ? "连接中..." : "未连接"}
          </p>
        </div>
        <span
          className={`px-2 py-0.5 rounded-full text-xs font-medium border ${
            wsStatus === "open"
              ? "bg-green-100 text-green-600 border-green-200"
              : "bg-gray-100 text-gray-400 border-gray-200"
          }`}
        >
          {wsStatus === "open" ? "在线" : "离线"}
        </span>
      </div>

      {/* 消息流 */}
      <GlassCard hover={false} className="flex-1 overflow-y-auto mb-4 p-4">
        {isLoading && <LoadingSpinner text="加载历史消息..." />}
        {!isLoading && allMessages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-twilight-400">
            <MessageCircle className="w-12 h-12 mb-3 opacity-40" />
            <p className="text-sm">开始和角色对话吧</p>
          </div>
        )}
        <div className="space-y-4">
          {allMessages.map((m: any) => (
            <motion.div
              key={m.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className={`flex gap-3 ${m.sender === "user" ? "flex-row-reverse" : ""}`}
            >
              <div
                className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                  m.sender === "user"
                    ? "bg-gradient-to-br from-sky-soft-300 to-sky-soft-500 text-white"
                    : "bg-gradient-to-br from-sakura-300 to-sakura-500 text-white"
                }`}
              >
                {m.sender === "user" ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
              </div>
              <div className={`max-w-[75%] ${m.sender === "user" ? "items-end" : "items-start"}`}>
                <div
                  className={`px-4 py-2.5 rounded-2xl text-sm leading-relaxed ${
                    m.sender === "user"
                      ? "bg-gradient-to-r from-sky-soft-400 to-sky-soft-500 text-white rounded-tr-md"
                      : "bg-white/70 text-twilight-600 rounded-tl-md border border-white/40"
                  }`}
                >
                  {m.share_type && (
                    <span className="inline-block mr-2 px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-gradient-to-r from-sakura-400 to-twilight-400 text-white align-middle">
                      分享
                    </span>
                  )}
                  {m.content}
                </div>
                <span className="text-[10px] text-twilight-300 mt-1 block px-1">
                  {new Date(m.created_at).toLocaleTimeString("zh-CN", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
            </motion.div>
          ))}
          {sendMutation.isPending && (
            <div className="flex gap-3 flex-row-reverse">
              <div className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0 bg-gradient-to-br from-sky-soft-300 to-sky-soft-500 text-white">
                <User className="w-4 h-4" />
              </div>
              <div className="px-4 py-2.5 rounded-2xl text-sm bg-gradient-to-r from-sky-soft-300 to-sky-soft-400 text-white rounded-tr-md animate-pulse">
                发送中...
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </GlassCard>

      {/* 输入区 */}
      <div className="flex gap-3 items-end">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息..."
          rows={1}
          className="flex-1 px-4 py-3 rounded-2xl bg-white/70 border border-white/40 text-sm text-twilight-600 placeholder-twilight-300 resize-none focus:outline-none focus:border-sakura-300/50 transition-colors"
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || wsStatus !== "open"}
          className="p-3 rounded-2xl bg-gradient-to-r from-sakura-400 to-sakura-500 text-white disabled:opacity-40 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-sakura-400/30 transition-all"
        >
          <Send className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}
