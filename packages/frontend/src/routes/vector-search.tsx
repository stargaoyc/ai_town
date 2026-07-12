import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { Search, Clock, Star, Sliders } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  StatusBadge,
  AnimeInput,
  AnimeButton,
  ProgressBar,
} from "@/components/ui";
import { useCharacters, useVectorSearch } from "@/lib/queries";
import type { VectorSearchResult } from "@/lib/api";

export const Route = createFileRoute("/vector-search")({
  component: VectorSearchPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.05 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

// 来源类型对应的标签样式与文案
const sourceTypeMap: Record<
  string,
  { label: string; color: string; emoji: string }
> = {
  action: {
    label: "行为",
    color: "bg-sky-soft-100 text-sky-soft-600 border-sky-soft-200/50",
    emoji: "⚡",
  },
  conversation: {
    label: "对话",
    color: "bg-sakura-100 text-sakura-600 border-sakura-200/50",
    emoji: "💬",
  },
  reflection: {
    label: "反思",
    color: "bg-twilight-100 text-twilight-500 border-twilight-200/50",
    emoji: "💭",
  },
};

// 格式化时间
function formatTime(dateStr: string): string {
  return new Date(dateStr).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// 根据相似度分数返回渐变色配置（高=绿，中=黄，低=红）
function similarityColor(sim: number): { bar: string; text: string } {
  if (sim >= 0.75) {
    return {
      bar: "from-emerald-300 to-emerald-500",
      text: "text-emerald-600",
    };
  }
  if (sim >= 0.5) {
    return {
      bar: "from-amber-300 to-amber-500",
      text: "text-amber-600",
    };
  }
  return {
    bar: "from-red-300 to-red-500",
    text: "text-red-500",
  };
}

function VectorSearchPage() {
  const [selectedCharacter, setSelectedCharacter] = useState("");
  const [query, setQuery] = useState("");
  // top_k 滑块（1-20，默认 10）
  const [topK, setTopK] = useState(10);

  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  const searchMutation = useVectorSearch();
  const results: VectorSearchResult[] = searchMutation.data?.data ?? [];

  // 统计：平均相似度、最高相似度
  const resultStats = useMemo(() => {
    if (results.length === 0) return { avg: 0, max: 0 };
    const sum = results.reduce((s, r) => s + r.similarity, 0);
    const max = results.reduce((m, r) => Math.max(m, r.similarity), 0);
    return { avg: sum / results.length, max };
  }, [results]);

  // 执行搜索
  const handleSearch = () => {
    if (!selectedCharacter || !query.trim()) return;
    searchMutation.mutate({
      characterId: selectedCharacter,
      query: query.trim(),
      topK,
    });
  };

  // 是否已有搜索结果
  const hasSearched =
    !searchMutation.isPending &&
    !searchMutation.isError &&
    results.length === 0 &&
    searchMutation.isSuccess;

  return (
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="向量检索测试"
          subtitle="测试角色的记忆检索能力，基于语义相似度匹配"
          icon="🔍"
        />

        {/* 检索控制面板 */}
        <GlassCard hover={false}>
          <div className="space-y-4">
            {/* 角色选择 + 查询输入 */}
            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-twilight-500 font-medium mb-2">
                  选择角色
                </label>
                {charsLoading ? (
                  <div className="text-sm text-twilight-400">加载角色中...</div>
                ) : (
                  <select
                    value={selectedCharacter}
                    onChange={(e) => setSelectedCharacter(e.target.value)}
                    className="w-full px-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent focus:bg-white/80 transition-all"
                  >
                    <option value="">— 请选择角色 —</option>
                    {characters.map((char) => (
                      <option key={char.id} value={char.id}>
                        {char.name}（{char.id}）
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div>
                <label className="block text-sm text-twilight-500 font-medium mb-2">
                  查询文本
                </label>
                <AnimeInput
                  placeholder="输入自然语言查询，如「昨天和谁一起吃了饭」"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSearch();
                  }}
                  icon={<Search className="w-4 h-4" />}
                />
              </div>
            </div>

            {/* top_k 滑块（1-20，默认 10） */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm text-twilight-500 font-medium flex items-center gap-2">
                  <Sliders className="w-4 h-4" />
                  top_k（返回结果数）
                </label>
                <span className="text-sm font-semibold text-sakura-600">
                  {topK}
                </span>
              </div>
              <input
                type="range"
                min={1}
                max={20}
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value))}
                className="w-full h-2 rounded-full appearance-none bg-gradient-to-r from-sakura-200 to-twilight-200 outline-none cursor-pointer accent-sakura-500"
              />
              <div className="flex justify-between text-xs text-twilight-300 mt-1">
                <span>1</span>
                <span>20</span>
              </div>
            </div>

            {/* 搜索按钮 */}
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                {searchMutation.isSuccess && (
                  <StatusBadge status="ok" label="检索完成" />
                )}
              </div>
              <AnimeButton
                onClick={handleSearch}
                disabled={
                  !selectedCharacter ||
                  !query.trim() ||
                  searchMutation.isPending
                }
              >
                {searchMutation.isPending ? "检索中..." : "🔍 搜索"}
              </AnimeButton>
            </div>
          </div>
        </GlassCard>

        {/* 检索结果统计 */}
        {results.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <StatCard
              title="结果数"
              value={results.length}
              icon="📊"
              color="sakura"
            />
            <StatCard
              title="平均相似度"
              value={`${(resultStats.avg * 100).toFixed(1)}%`}
              icon="📈"
              color="sky"
            />
            <StatCard
              title="最高相似度"
              value={`${(resultStats.max * 100).toFixed(1)}%`}
              icon="🎯"
              color="twilight"
            />
          </div>
        )}

        {/* 加载与错误状态 */}
        {searchMutation.isPending && (
          <LoadingSpinner text="正在向量检索..." />
        )}
        {searchMutation.isError && (
          <ErrorDisplay error={searchMutation.error as Error} />
        )}

        {/* 空状态：未搜索时 */}
        {!searchMutation.isPending &&
          !searchMutation.isError &&
          results.length === 0 &&
          !hasSearched && (
            <EmptyState
              icon="🔍"
              title="输入查询文本"
              subtitle="输入查询文本，测试角色的记忆检索能力"
            />
          )}

        {/* 搜索无结果 */}
        {hasSearched && (
          <EmptyState
            icon="🤔"
            title="未找到相关记忆"
            subtitle="尝试更换查询文本或选择其他角色"
          />
        )}

        {/* 搜索结果列表 */}
        {results.length > 0 && (
          <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="space-y-4"
          >
            {results.map((result) => {
              const src = sourceTypeMap[result.source_type] ?? {
                label: result.source_type,
                color: "bg-gray-100 text-gray-500 border-gray-200/50",
                emoji: "📝",
              };
              const simPct = Math.round(result.similarity * 100);
              const sc = similarityColor(result.similarity);
              return (
                <motion.div key={result.id} variants={item}>
                  <GlassCard className="space-y-3" hover>
                    {/* 顶部：来源类型标签 + 时间 */}
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium border ${src.color}`}
                      >
                        {src.emoji} {src.label}
                      </span>
                      <span className="text-xs text-twilight-400 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {formatTime(result.timestamp)}
                      </span>
                    </div>

                    {/* 记忆内容 */}
                    <p className="text-sm text-twilight-700 leading-relaxed whitespace-pre-wrap">
                      {result.content}
                    </p>

                    {/* 相似度分数 - 渐变色进度条（高=绿，中=黄，低=红） */}
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-xs text-twilight-400 font-medium">
                          相似度
                        </span>
                        <span className={`text-xs font-bold ${sc.text}`}>
                          {simPct}%
                        </span>
                      </div>
                      <div className="w-full bg-white/50 rounded-full h-2.5 overflow-hidden shadow-inner border border-white/30">
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${simPct}%` }}
                          transition={{ duration: 0.8, ease: "easeOut" }}
                          className={`h-full bg-gradient-to-r ${sc.bar} rounded-full shadow-md`}
                        />
                      </div>
                    </div>

                    {/* 重要性 - 用 ProgressBar 展示 */}
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-xs text-twilight-400 font-medium flex items-center gap-1">
                          <Star className="w-3.5 h-3.5 text-amber-400" />
                          重要性
                        </span>
                        <span className="text-xs font-semibold text-twilight-500">
                          {result.importance} / 10
                        </span>
                      </div>
                      <ProgressBar
                        value={result.importance}
                        max={10}
                        color="twilight"
                      />
                    </div>

                    {/* 反思状态 */}
                    {result.is_reflected && (
                      <div className="pt-1">
                        <StatusBadge status="ok" label="已反思" />
                      </div>
                    )}
                  </GlassCard>
                </motion.div>
              );
            })}
          </motion.div>
        )}
      </div>
  );
}
