import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { Search, Star, Brain, Filter } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  AnimeInput,
  ProgressBar,
} from "@/components/ui";
import { useCharacters, useMemories } from "@/lib/queries";

export const Route = createFileRoute("/memories")({
  component: MemoriesPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.05 } },
};

const item = {
  hidden: { opacity: 0, x: -20 },
  show: { opacity: 1, x: 0 },
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

function MemoriesPage() {
  const [selectedCharacter, setSelectedCharacter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  // 重要性筛选阈值，默认 1（显示全部）
  const [minImportance, setMinImportance] = useState(1);

  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  const {
    data: memoriesData,
    isLoading,
    error,
  } = useMemories(selectedCharacter, 100);
  const memories = memoriesData?.data ?? [];

  // 前端过滤：搜索 + 重要性筛选
  const filteredMemories = useMemo(() => {
    return memories
      .filter((m) => m.importance >= minImportance)
      .filter((m) =>
        searchQuery.trim()
          ? m.content.toLowerCase().includes(searchQuery.toLowerCase())
          : true,
      )
      .sort(
        (a, b) =>
          new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
      );
  }, [memories, minImportance, searchQuery]);

  // 渲染重要性星级（1-10）
  const renderStars = (importance: number) => {
    return (
      <div className="flex items-center gap-0.5">
        {Array.from({ length: 10 }).map((_, i) => (
          <Star
            key={i}
            className={`w-3 h-3 ${
              i < importance
                ? "text-amber-400 fill-amber-400"
                : "text-twilight-200"
            }`}
          />
        ))}
        <span className="text-xs text-twilight-400 ml-1">{importance}/10</span>
      </div>
    );
  };

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="记忆时间线"
        subtitle="浏览角色记忆，按重要性与内容筛选"
        icon="🧠"
        backTo="/admin"
        backLabel="返回管理"
      />

      {/* 顶部统计 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          title="记忆总数"
          value={memories.length}
          icon="📚"
          color="sakura"
        />
        <StatCard
          title="筛选后"
          value={filteredMemories.length}
          icon="🔍"
          color="sky"
        />
        <StatCard
          title="角色数"
          value={characters.length}
          icon="👥"
          color="twilight"
        />
      </div>

      {/* 控制栏：角色选择 + 搜索 + 重要性筛选 */}
      <GlassCard hover={false}>
        <div className="space-y-4">
          {/* 角色选择 + 搜索 */}
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
                搜索记忆内容
              </label>
              <AnimeInput
                placeholder="输入关键词搜索..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                icon={<Search className="w-4 h-4" />}
              />
            </div>
          </div>
          {/* 重要性筛选滑块 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm text-twilight-500 font-medium flex items-center gap-2">
                <Filter className="w-4 h-4" />
                最低重要性
              </label>
              <span className="text-sm font-semibold text-sakura-600">
                {minImportance} / 10
              </span>
            </div>
            <input
              type="range"
              min={1}
              max={10}
              value={minImportance}
              onChange={(e) => setMinImportance(Number(e.target.value))}
              className="w-full h-2 rounded-full appearance-none bg-gradient-to-r from-sakura-200 to-twilight-200 outline-none cursor-pointer accent-sakura-500"
            />
            <div className="mt-2">
              <ProgressBar value={minImportance} max={10} color="sakura" />
            </div>
          </div>
        </div>
      </GlassCard>

      {!selectedCharacter && (
        <EmptyState
          icon="👆"
          title="请先选择一个角色"
          subtitle="选择角色后将展示其记忆时间线"
        />
      )}

      {selectedCharacter && isLoading && (
        <LoadingSpinner text="正在加载记忆..." />
      )}
      {selectedCharacter && error && <ErrorDisplay error={error} />}

      {selectedCharacter &&
        !isLoading &&
        !error &&
        filteredMemories.length === 0 && (
          <EmptyState
            icon="🧠"
            title="暂无记忆数据"
            subtitle="该角色还没有符合条件的记忆记录"
          />
        )}

      {/* 时间线 */}
      {filteredMemories.length > 0 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="relative"
        >
          {/* 垂直时间轴线 */}
          <div className="absolute left-5 top-0 bottom-0 w-0.5 bg-gradient-to-b from-sakura-300 via-twilight-300 to-sky-soft-300" />

          <div className="space-y-4">
            {filteredMemories.map((memory) => {
              const src = sourceTypeMap[memory.source_type] ?? {
                label: memory.source_type,
                color: "bg-gray-100 text-gray-500 border-gray-200/50",
                emoji: "📝",
              };
              return (
                <motion.div
                  key={memory.id}
                  variants={item}
                  className="relative pl-14"
                >
                  {/* 时间轴节点 */}
                  <div
                    className={`absolute left-2 top-4 w-7 h-7 rounded-full flex items-center justify-center text-sm shadow-md border-2 border-white/60 ${
                      memory.importance >= 7
                        ? "bg-gradient-to-br from-sakura-400 to-sakura-500"
                        : memory.importance >= 4
                          ? "bg-gradient-to-br from-twilight-300 to-twilight-400"
                          : "bg-gradient-to-br from-sky-soft-300 to-sky-soft-400"
                    }`}
                  >
                    {src.emoji}
                  </div>

                  <GlassCard className="space-y-2" hover>
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <div className="flex items-center gap-2">
                        {/* 来源类型标签 */}
                        <span
                          className={`px-2 py-0.5 rounded-full text-xs font-medium border ${src.color}`}
                        >
                          {src.label}
                        </span>
                        {/* 反思状态 */}
                        {memory.is_reflected && (
                          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-600 border border-emerald-200/50">
                            ✓ 已反思
                          </span>
                        )}
                      </div>
                      <span className="text-xs text-twilight-400">
                        {formatTime(memory.timestamp)}
                      </span>
                    </div>
                    <p className="text-sm text-twilight-700 leading-relaxed whitespace-pre-wrap">
                      {memory.content}
                    </p>
                    <div className="pt-1">{renderStars(memory.importance)}</div>
                  </GlassCard>
                </motion.div>
              );
            })}
          </div>
        </motion.div>
      )}

      {/* 提示信息 */}
      {selectedCharacter && !isLoading && memories.length > 0 && (
        <GlassCard hover={false}>
          <div className="flex items-start gap-3 text-sm text-twilight-400">
            <Brain className="w-5 h-5 text-sakura-400 mt-0.5 shrink-0" />
            <div>
              <div className="font-medium text-twilight-500 mb-1">记忆说明</div>
              <ul className="space-y-1">
                <li>• 记忆按时间倒序排列，最新记忆在最上方</li>
                <li>• 重要性范围为 1-10，数值越高表示越关键</li>
                <li>
                  •
                  来源类型：⚡行为（action）、💬对话（conversation）、💭反思（reflection）
                </li>
                <li>• 使用搜索框可按内容关键词过滤记忆</li>
              </ul>
            </div>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
