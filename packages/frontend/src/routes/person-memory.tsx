import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { User, Search, Flame, Clock, Heart, MessageCircle } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  AnimeInput,
} from "@/components/ui";
import { useCharacters, usePersonMemoriesList } from "@/lib/queries";
import type { PersonMemoryEntry } from "@/lib/api";

export const Route = createFileRoute("/person-memory")({
  component: PersonMemoryPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

function formatTime(dateStr?: string): string {
  if (!dateStr) return "—";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getHeatLevel(heat: number): { label: string; color: string } {
  if (heat >= 50)
    return {
      label: "热络",
      color: "bg-red-100 text-red-600 border-red-200/50",
    };
  if (heat >= 20)
    return {
      label: "熟悉",
      color: "bg-amber-100 text-amber-700 border-amber-200/50",
    };
  if (heat >= 10)
    return {
      label: "认识",
      color: "bg-sky-soft-100 text-sky-soft-600 border-sky-soft-200/50",
    };
  return {
    label: "初识",
    color: "bg-gray-100 text-gray-600 border-gray-200/50",
  };
}

function PersonMemoryPage() {
  const [selectedCharacter, setSelectedCharacter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  const {
    data: memoriesData,
    isLoading,
    error,
  } = usePersonMemoriesList(selectedCharacter, 100);
  const memories = memoriesData?.data ?? [];

  // 前端搜索过滤
  const filteredMemories = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return memories;
    return memories.filter(
      (m) =>
        m.user_id.toLowerCase().includes(q) ||
        m.content.toLowerCase().includes(q) ||
        (m.platform || "").toLowerCase().includes(q),
    );
  }, [memories, searchQuery]);

  // 统计
  const stats = useMemo(() => {
    const totalHeat = memories.reduce((sum, m) => sum + (m.heat || 0), 0);
    const avgHeat =
      memories.length > 0 ? Math.round(totalHeat / memories.length) : 0;
    const hotUsers = memories.filter((m) => m.heat >= 20).length;
    return {
      total: memories.length,
      avgHeat,
      hotUsers,
    };
  }, [memories]);

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="角色对用户的记忆"
        subtitle="角色视角下对每个用户的独立记忆与热度"
        icon="💭"
        backTo="/admin"
        backLabel="返回管理"
      />

      {/* 顶部统计 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          title="记忆用户数"
          value={stats.total}
          icon="👤"
          color="sakura"
        />
        <StatCard
          title="平均热度"
          value={stats.avgHeat}
          icon="🔥"
          color="twilight"
        />
        <StatCard
          title="热络用户"
          value={stats.hotUsers}
          icon="❤️"
          color="sky"
        />
      </div>

      {/* 控制栏 */}
      <GlassCard hover={false}>
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
                    {char.name}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div>
            <label className="block text-sm text-twilight-500 font-medium mb-2">
              搜索用户记忆
            </label>
            <AnimeInput
              placeholder="搜索用户 ID、内容、平台..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              icon={<Search className="w-4 h-4" />}
            />
          </div>
        </div>
      </GlassCard>

      {!selectedCharacter && (
        <EmptyState
          icon="👆"
          title="请先选择一个角色"
          subtitle="选择角色后将展示其对用户的记忆"
        />
      )}

      {selectedCharacter && isLoading && (
        <LoadingSpinner text="正在加载用户记忆..." />
      )}
      {selectedCharacter && !isLoading && error && (
        <ErrorDisplay error={error} />
      )}

      {selectedCharacter && !isLoading && !error && memories.length === 0 && (
        <EmptyState
          icon="💭"
          title="暂无用户记忆"
          subtitle="用户与角色互动后将自动生成记忆"
        />
      )}

      {selectedCharacter &&
        !isLoading &&
        !error &&
        memories.length > 0 &&
        filteredMemories.length === 0 && (
          <EmptyState
            icon="🔍"
            title="未匹配到记忆"
            subtitle="尝试更换搜索关键词"
          />
        )}

      {/* 记忆列表 */}
      {filteredMemories.length > 0 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="space-y-4"
        >
          {filteredMemories.map((memory: PersonMemoryEntry, idx: number) => {
            const heatInfo = getHeatLevel(memory.heat || 0);
            return (
              <motion.div key={memory.id || idx} variants={item}>
                <GlassCard className="space-y-3" hover>
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-sakura-100 text-sakura-600 border border-sakura-200/50">
                        <User className="w-3 h-3" />
                        {memory.user_id}
                      </span>
                      <span
                        className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${heatInfo.color}`}
                      >
                        <Flame className="w-3 h-3" />
                        {heatInfo.label} · {memory.heat || 0}
                      </span>
                      {memory.platform && (
                        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-twilight-100 text-twilight-500 border border-twilight-200/50">
                          <MessageCircle className="w-3 h-3" />
                          {memory.platform}
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-twilight-400 flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {formatTime(memory.last_interaction_at)}
                    </span>
                  </div>

                  <div className="flex items-start gap-2">
                    <Heart className="w-4 h-4 text-sakura-400 mt-0.5 shrink-0" />
                    <p className="text-sm text-twilight-700 leading-relaxed whitespace-pre-wrap flex-1">
                      {memory.content}
                    </p>
                  </div>

                  {(memory.created_at || memory.updated_at) && (
                    <div className="text-xs text-twilight-400 flex items-center gap-3 pt-1 border-t border-sakura-200/30">
                      {memory.created_at && (
                        <span>创建：{formatTime(memory.created_at)}</span>
                      )}
                      {memory.updated_at &&
                        memory.updated_at !== memory.created_at && (
                          <span>更新：{formatTime(memory.updated_at)}</span>
                        )}
                    </div>
                  )}
                </GlassCard>
              </motion.div>
            );
          })}
        </motion.div>
      )}

      {/* 说明 */}
      {selectedCharacter && !isLoading && memories.length > 0 && (
        <GlassCard hover={false}>
          <div className="flex items-start gap-3 text-sm text-twilight-400">
            <User className="w-5 h-5 text-sakura-400 mt-0.5 shrink-0" />
            <div>
              <div className="font-medium text-twilight-500 mb-1">记忆说明</div>
              <ul className="space-y-1">
                <li>
                  • 角色对每个用户维护独立的记忆，含偏好、关系进展、共同话题
                </li>
                <li>
                  • 热度机制：每次交互热度
                  +1，长期不交互不衰减（按交互频率排序）
                </li>
                <li>• 热度 ≥ 20 为"熟悉"，≥ 50 为"热络"</li>
                <li>• 记忆内容由 LLM 根据对话历史自动更新</li>
              </ul>
            </div>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
