import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Download, FileJson, FileText, User, Bot, Clock } from "lucide-react";
import {
  NavLayout,
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  AnimeButton,
  StatusBadge,
} from "@/components/ui";
import { useCharacters } from "@/lib/queries";
import { api } from "@/lib/api";
import type { Message } from "@/lib/api";

export const Route = createFileRoute("/export")({
  component: ExportPage,
});

// 导出格式选项
type ExportFormat = "json" | "markdown";

// 发送者显示名称映射
function senderLabel(sender: string): string {
  switch (sender) {
    case "user":
      return "用户";
    case "character":
      return "角色";
    case "system":
      return "系统";
    default:
      return sender;
  }
}

// 发送者图标
function senderIcon(sender: string) {
  if (sender === "user") return <User className="w-3.5 h-3.5" />;
  if (sender === "character") return <Bot className="w-3.5 h-3.5" />;
  return null;
}

// 格式化时间戳为可读字符串
function formatTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

// 将消息列表转换为 Markdown 格式
function toMarkdown(messages: Message[], characterName: string): string {
  const header = `# 聊天记录 — ${characterName}\n\n> 导出时间：${new Date().toLocaleString("zh-CN")}\n> 消息总数：${messages.length}\n\n---\n\n`;
  const body = messages
    .map((m) => {
      const time = formatTime(m.created_at);
      const sender = senderLabel(m.sender);
      return `**[${time}] ${sender}:** ${m.content}`;
    })
    .join("\n\n");
  return header + body + "\n";
}

function ExportPage() {
  // 获取角色列表用于下拉选择
  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  // 当前选中的角色 ID
  const [characterId, setCharacterId] = useState<string>("");
  // 导出格式
  const [format, setFormat] = useState<ExportFormat>("json");

  // 获取聊天记录（最多 500 条）
  const { data, isLoading, error } = useQuery({
    queryKey: ["exportHistory", characterId],
    queryFn: () => api.getHistory(characterId, 500),
    enabled: !!characterId,
  });
  const messages = data?.data ?? [];

  // 选中的角色对象（用于获取名称）
  const selectedCharacter = useMemo(
    () => characters.find((c) => c.id === characterId),
    [characters, characterId],
  );

  // 统计：总消息数、用户消息数、角色消息数
  const stats = useMemo(() => {
    const userMsgs = messages.filter((m) => m.sender === "user").length;
    const charMsgs = messages.filter((m) => m.sender === "character").length;
    return { total: messages.length, user: userMsgs, character: charMsgs };
  }, [messages]);

  // 预览：前 10 条消息
  const previewMessages = useMemo(() => messages.slice(0, 10), [messages]);

  // 触发文件下载
  const downloadFile = (content: string, filename: string, mime: string) => {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  // 执行导出
  const handleExport = () => {
    if (messages.length === 0 || !characterId) return;
    const safeName =
      selectedCharacter?.name?.replace(/[^\w\u4e00-\u9fa5-]/g, "_") ??
      characterId;
    const timestamp = new Date().toISOString().slice(0, 10);

    if (format === "json") {
      const content = JSON.stringify(
        {
          character: selectedCharacter?.name ?? characterId,
          character_id: characterId,
          exported_at: new Date().toISOString(),
          total_messages: messages.length,
          messages,
        },
        null,
        2,
      );
      downloadFile(
        content,
        `chat-${safeName}-${timestamp}.json`,
        "application/json",
      );
    } else {
      const content = toMarkdown(
        messages,
        selectedCharacter?.name ?? characterId,
      );
      downloadFile(
        content,
        `chat-${safeName}-${timestamp}.md`,
        "text/markdown",
      );
    }
  };

  return (
    <NavLayout>
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="聊天记录导出"
          subtitle="导出角色对话记录为 JSON 或 Markdown 文件"
          icon="📦"
        />

        {/* 角色选择与格式选择 */}
        <GlassCard hover={false}>
          <div className="grid md:grid-cols-2 gap-4">
            {/* 角色选择器 */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-twilight-500 flex items-center gap-1.5">
                <User className="w-4 h-4 text-sakura-400" />
                选择角色
              </label>
              <select
                value={characterId}
                onChange={(e) => setCharacterId(e.target.value)}
                disabled={charsLoading}
                className="w-full px-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent focus:bg-white/80 transition-all disabled:opacity-50"
              >
                <option value="">
                  {charsLoading ? "加载角色中..." : "请选择角色"}
                </option>
                {characters.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}（{c.id}）
                  </option>
                ))}
              </select>
            </div>

            {/* 导出格式选择 */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-twilight-500 flex items-center gap-1.5">
                <FileText className="w-4 h-4 text-sakura-400" />
                导出格式
              </label>
              <div className="flex gap-2">
                <motion.button
                  whileHover={{ scale: 1.03 }}
                  whileTap={{ scale: 0.97 }}
                  onClick={() => setFormat("json")}
                  className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-medium border transition-all ${
                    format === "json"
                      ? "bg-gradient-to-r from-sakura-400 to-sakura-500 text-white border-transparent shadow-md shadow-sakura-400/30"
                      : "bg-white/60 text-twilight-600 border-sakura-200/50 hover:border-sakura-300/50"
                  }`}
                >
                  <FileJson className="w-4 h-4" />
                  JSON
                </motion.button>
                <motion.button
                  whileHover={{ scale: 1.03 }}
                  whileTap={{ scale: 0.97 }}
                  onClick={() => setFormat("markdown")}
                  className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-medium border transition-all ${
                    format === "markdown"
                      ? "bg-gradient-to-r from-sakura-400 to-sakura-500 text-white border-transparent shadow-md shadow-sakura-400/30"
                      : "bg-white/60 text-twilight-600 border-sakura-200/50 hover:border-sakura-300/50"
                  }`}
                >
                  <FileText className="w-4 h-4" />
                  Markdown
                </motion.button>
              </div>
            </div>
          </div>

          {/* 导出按钮 */}
          <div className="mt-4 flex items-center gap-3 flex-wrap">
            <AnimeButton
              onClick={handleExport}
              disabled={!characterId || messages.length === 0}
            >
              <span className="flex items-center gap-2">
                <Download className="w-4 h-4" />
                导出 {format === "json" ? "JSON" : "Markdown"} 文件
              </span>
            </AnimeButton>
            {characterId && messages.length > 0 && (
              <StatusBadge
                status="ok"
                label={`共 ${messages.length} 条消息可导出`}
              />
            )}
          </div>
        </GlassCard>

        {/* 未选择角色提示 */}
        {!characterId && !charsLoading && (
          <EmptyState
            icon="📦"
            title="请先选择一个角色"
            subtitle="选择角色后将加载该角色的聊天记录，支持导出为 JSON 或 Markdown"
          />
        )}

        {/* 加载与错误状态 */}
        {characterId && isLoading && (
          <LoadingSpinner text="正在加载聊天记录..." />
        )}
        {characterId && error && <ErrorDisplay error={error} />}

        {/* 统计卡片 */}
        {characterId && !isLoading && !error && messages.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="grid grid-cols-1 md:grid-cols-3 gap-4"
          >
            <StatCard
              title="总消息数"
              value={stats.total}
              icon="💬"
              color="sakura"
            />
            <StatCard
              title="用户消息"
              value={stats.user}
              icon="👤"
              color="sky"
            />
            <StatCard
              title="角色消息"
              value={stats.character}
              icon="🤖"
              color="twilight"
            />
          </motion.div>
        )}

        {/* 空数据提示 */}
        {characterId &&
          !isLoading &&
          !error &&
          messages.length === 0 && (
            <EmptyState
              icon="📭"
              title="该角色暂无聊天记录"
              subtitle="当用户与该角色开始对话后，聊天记录将显示在这里"
            />
          )}

        {/* 导出预览 */}
        {characterId && !isLoading && !error && previewMessages.length > 0 && (
          <GlassCard hover={false}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-sakura-600 flex items-center gap-2 text-lg">
                <FileText className="w-5 h-5" />
                导出预览
              </h3>
              <span className="text-xs text-twilight-400">
                显示前 10 条 / 共 {messages.length} 条
              </span>
            </div>

            {/* Markdown 格式预览 */}
            {format === "markdown" ? (
              <div className="space-y-3">
                {previewMessages.map((m) => (
                  <div
                    key={m.id}
                    className="p-3 rounded-xl bg-white/50 border border-white/40 text-sm"
                  >
                    <span className="font-semibold text-sakura-600">
                      [{formatTime(m.created_at)}] {senderLabel(m.sender)}:
                    </span>{" "}
                    <span className="text-twilight-600">{m.content}</span>
                  </div>
                ))}
              </div>
            ) : (
              /* JSON 格式预览 */
              <pre className="p-4 rounded-2xl bg-twilight-900/90 text-sakura-100 text-xs font-mono overflow-x-auto max-h-96 overflow-y-auto">
                {JSON.stringify(previewMessages, null, 2)}
              </pre>
            )}

            {/* 预览底部信息 */}
            <div className="mt-4 flex items-center gap-2 text-xs text-twilight-400">
              <Clock className="w-3.5 h-3.5" />
              <span>
                时间范围：{formatTime(messages[0]?.created_at ?? "")}
                {messages.length > 1 && (
                  <>
                    {" "}
                    ~{" "}
                    {formatTime(
                      messages[messages.length - 1]?.created_at ?? "",
                    )}
                  </>
                )}
              </span>
            </div>
          </GlassCard>
        )}
      </div>
    </NavLayout>
  );
}
