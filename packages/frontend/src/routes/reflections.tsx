import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { Brain, Clock, Sparkles, Search } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  AnimeInput,
} from "@/components/ui";
import { useCharacters, useReflections } from "@/lib/queries";

export const Route = createFileRoute("/reflections")({
  component: ReflectionsPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

// 格式化时间
function formatTime(dateStr: string): string {
  if (!dateStr) return "—";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// 解析反思内容：将以 "- " 开头的行作为独立认知点
function parseReflection(content: string): {
  points: string[];
  intro: string;
} {
  const lines = content.split("\n");
  const points: string[] = [];
  const introLines: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    // 支持 "- " 和 "• " 两种列表前缀
    if (trimmed.startsWith("- ") || trimmed.startsWith("• ")) {
      const text = trimmed.replace(/^[-•]\s+/, "").trim();
      if (text) points.push(text);
    } else if (trimmed) {
      introLines.push(trimmed);
    }
  }
  return { points, intro: introLines.join(" ") };
}

function ReflectionsPage() {
  const [selectedCharacter, setSelectedCharacter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  const {
    data: reflectionsData,
    isLoading,
    error,
  } = useReflections(selectedCharacter);
  const reflections = reflectionsData?.data ?? [];

  // 解析每条反思内容
  const parsedReflections = useMemo(() => {
    return reflections.map((r) => ({
      ...r,
      parsed: parseReflection(r.content),
    }));
  }, [reflections]);

  // 前端搜索过滤
  const filteredReflections = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return parsedReflections;
    return parsedReflections.filter(
      (r) =>
        r.content.toLowerCase().includes(q) ||
        r.parsed.points.some((p) => p.toLowerCase().includes(q)),
    );
  }, [parsedReflections, searchQuery]);

  // 认知点总数
  const totalPoints = parsedReflections.reduce(
    (sum, r) => sum + r.parsed.points.length,
    0,
  );

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="反思查看器"
        subtitle="角色的自我反思与认知沉淀记录"
        icon="💭"
        backTo="/admin"
        backLabel="返回管理"
      />

      {/* 顶部统计 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          title="反思总数"
          value={reflections.length}
          icon="💭"
          color="sakura"
        />
        <StatCard
          title="认知点"
          value={totalPoints}
          icon="✨"
          color="twilight"
        />
        <StatCard
          title="角色数"
          value={characters.length}
          icon="👥"
          color="sky"
        />
      </div>

      {/* 控制栏：角色选择 + 搜索 */}
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
                    {char.name}（{char.id}）
                  </option>
                ))}
              </select>
            )}
          </div>
          <div>
            <label className="block text-sm text-twilight-500 font-medium mb-2">
              搜索反思内容
            </label>
            <AnimeInput
              placeholder="输入关键词搜索认知点..."
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
          subtitle="选择角色后将展示其反思记录"
        />
      )}

      {selectedCharacter && isLoading && (
        <LoadingSpinner text="正在加载反思记录..." />
      )}
      {selectedCharacter && !isLoading && error && (
        <ErrorDisplay error={error} />
      )}

      {selectedCharacter &&
        !isLoading &&
        !error &&
        reflections.length === 0 && (
          <EmptyState
            icon="💭"
            title="暂无反思记录"
            subtitle="角色需要积累 20 条记忆后才会触发反思"
          />
        )}

      {selectedCharacter &&
        !isLoading &&
        !error &&
        reflections.length > 0 &&
        filteredReflections.length === 0 && (
          <EmptyState
            icon="🔍"
            title="未匹配到反思"
            subtitle="尝试更换搜索关键词"
          />
        )}

      {/* 反思列表 */}
      {filteredReflections.length > 0 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="space-y-4"
        >
          {filteredReflections.map((reflection) => (
            <motion.div key={reflection.id} variants={item}>
              <GlassCard className="space-y-3" hover>
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-twilight-100 text-twilight-500 border border-twilight-200/50">
                      💭 反思
                    </span>
                    {reflection.parsed.points.length > 0 && (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-sakura-100 text-sakura-600 border border-sakura-200/50">
                        {reflection.parsed.points.length} 个认知点
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-twilight-400 flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {formatTime(reflection.created_at)}
                  </span>
                </div>

                {/* 引言部分（非列表行） */}
                {reflection.parsed.intro && (
                  <p className="text-sm text-twilight-700 leading-relaxed">
                    {reflection.parsed.intro}
                  </p>
                )}

                {/* 认知点标签展示 */}
                {reflection.parsed.points.length > 0 && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {reflection.parsed.points.map((point, idx) => (
                      <motion.span
                        key={idx}
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ delay: idx * 0.03 }}
                        className="inline-flex items-start gap-1.5 px-3 py-1.5 rounded-xl bg-gradient-to-br from-sakura-50/80 to-twilight-50/80 border border-sakura-200/40 text-sm text-twilight-600"
                      >
                        <Sparkles className="w-3.5 h-3.5 text-sakura-400 mt-0.5 shrink-0" />
                        <span>{point}</span>
                      </motion.span>
                    ))}
                  </div>
                )}

                {/* 无列表行且有内容时直接展示原文 */}
                {reflection.parsed.points.length === 0 &&
                  !reflection.parsed.intro && (
                    <p className="text-sm text-twilight-700 leading-relaxed whitespace-pre-wrap">
                      {reflection.content}
                    </p>
                  )}
              </GlassCard>
            </motion.div>
          ))}
        </motion.div>
      )}

      {/* 说明 */}
      {selectedCharacter && !isLoading && reflections.length > 0 && (
        <GlassCard hover={false}>
          <div className="flex items-start gap-3 text-sm text-twilight-400">
            <Brain className="w-5 h-5 text-sakura-400 mt-0.5 shrink-0" />
            <div>
              <div className="font-medium text-twilight-500 mb-1">反思说明</div>
              <ul className="space-y-1">
                <li>• 反思是角色对记忆的归纳与认知沉淀</li>
                <li>• 以 "- " 开头的行作为独立认知点以标签形式展示</li>
                <li>• 角色需积累 20 条记忆后才会触发反思机制</li>
              </ul>
            </div>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
