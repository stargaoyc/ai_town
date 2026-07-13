import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import {
  BookOpen,
  Clock,
  Calendar,
  Sparkles,
  Search,
  Flame,
} from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  AnimeInput,
  AnimeButton,
} from "@/components/ui";
import { useCharacters, useDiaries, useGenerateDiary } from "@/lib/queries";
import type { DiaryEntry } from "@/lib/api";

export const Route = createFileRoute("/diaries")({
  component: DiariesPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

const PERIOD_LABELS = {
  day: {
    label: "日报",
    color: "bg-sakura-100 text-sakura-600 border-sakura-200/50",
    emoji: "📅",
  },
  week: {
    label: "周报",
    color: "bg-sky-soft-100 text-sky-soft-600 border-sky-soft-200/50",
    emoji: "🗓️",
  },
  month: {
    label: "月报",
    color: "bg-twilight-100 text-twilight-500 border-twilight-200/50",
    emoji: "📆",
  },
  year: {
    label: "年报",
    color: "bg-amber-100 text-amber-700 border-amber-200/50",
    emoji: "📊",
  },
} as const;

const DEFAULT_PERIOD = {
  label: "日记",
  color: "bg-sakura-100 text-sakura-600 border-sakura-200/50",
  emoji: "📖",
};

function getPeriodInfo(period: string) {
  return PERIOD_LABELS[period as keyof typeof PERIOD_LABELS] || DEFAULT_PERIOD;
}

function formatDate(dateStr: string): string {
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

function DiariesPage() {
  const [selectedCharacter, setSelectedCharacter] = useState("");
  const [periodFilter, setPeriodFilter] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  const {
    data: diariesData,
    isLoading,
    error,
  } = useDiaries(
    selectedCharacter,
    periodFilter ? { period: periodFilter, limit: 100 } : { limit: 100 },
  );
  const diaries = diariesData?.data ?? [];

  const generateDiary = useGenerateDiary();

  // 前端搜索过滤
  const filteredDiaries = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return diaries;
    return diaries.filter(
      (d) =>
        d.title.toLowerCase().includes(q) ||
        d.content.toLowerCase().includes(q) ||
        (d.mood || "").toLowerCase().includes(q),
    );
  }, [diaries, searchQuery]);

  // 统计
  const stats = useMemo(() => {
    const byPeriod: Record<string, number> = {};
    for (const d of diaries) {
      byPeriod[d.period] = (byPeriod[d.period] || 0) + 1;
    }
    return {
      total: diaries.length,
      byPeriod,
    };
  }, [diaries]);

  // 当前选中角色名
  const selectedCharName = useMemo(() => {
    const char = characters.find((c) => c.id === selectedCharacter);
    return char?.name || "";
  }, [characters, selectedCharacter]);

  async function handleGenerate(period: "day" | "week" | "month" | "year") {
    if (!selectedCharacter) return;
    setGenerating(true);
    setGenError(null);
    try {
      await generateDiary.mutateAsync({
        characterId: selectedCharacter,
        period,
        characterName: selectedCharName,
      });
    } catch (e) {
      setGenError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="角色日记"
        subtitle="基于记忆生成的角色视角叙事归档"
        icon="📖"
        backTo="/admin"
        backLabel="返回管理"
      />

      {/* 顶部统计 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard
          title="日记总数"
          value={stats.total}
          icon="📖"
          color="sakura"
        />
        <StatCard
          title="日报"
          value={stats.byPeriod.day || 0}
          icon="📅"
          color="sky"
        />
        <StatCard
          title="周报"
          value={stats.byPeriod.week || 0}
          icon="🗓️"
          color="twilight"
        />
        <StatCard
          title="月报"
          value={stats.byPeriod.month || 0}
          icon="📆"
          color="sakura"
        />
      </div>

      {/* 控制栏 */}
      <GlassCard hover={false}>
        <div className="grid md:grid-cols-3 gap-4">
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
              周期筛选
            </label>
            <select
              value={periodFilter}
              onChange={(e) => setPeriodFilter(e.target.value)}
              className="w-full px-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent focus:bg-white/80 transition-all"
            >
              <option value="">全部周期</option>
              <option value="day">日报</option>
              <option value="week">周报</option>
              <option value="month">月报</option>
              <option value="year">年报</option>
            </select>
          </div>
          <div>
            <label className="block text-sm text-twilight-500 font-medium mb-2">
              搜索日记
            </label>
            <AnimeInput
              placeholder="搜索标题、正文、情绪..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              icon={<Search className="w-4 h-4" />}
            />
          </div>
        </div>

        {/* 生成日记按钮组 */}
        {selectedCharacter && (
          <div className="mt-4 pt-4 border-t border-sakura-200/40">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm text-twilight-500 font-medium mr-2">
                生成日记：
              </span>
              {(["day", "week", "month", "year"] as const).map((p) => {
                const info = getPeriodInfo(p);
                return (
                  <AnimeButton
                    key={p}
                    onClick={() => handleGenerate(p)}
                    disabled={generating}
                    variant="secondary"
                    className="!px-3 !py-1.5 !text-sm"
                  >
                    {info.emoji} {info.label}
                  </AnimeButton>
                );
              })}
              {generating && (
                <span className="text-xs text-twilight-400 flex items-center gap-1">
                  <Sparkles className="w-3 h-3 animate-pulse" />
                  生成中...
                </span>
              )}
            </div>
            {genError && (
              <div className="mt-2 text-xs text-red-500 bg-red-50/60 border border-red-200/40 rounded-lg px-3 py-2">
                生成失败：{genError}
              </div>
            )}
          </div>
        )}
      </GlassCard>

      {!selectedCharacter && (
        <EmptyState
          icon="👆"
          title="请先选择一个角色"
          subtitle="选择角色后将展示其日记记录"
        />
      )}

      {selectedCharacter && isLoading && (
        <LoadingSpinner text="正在加载日记..." />
      )}
      {selectedCharacter && !isLoading && error && (
        <ErrorDisplay error={error} />
      )}

      {selectedCharacter && !isLoading && !error && diaries.length === 0 && (
        <EmptyState
          icon="📖"
          title="暂无日记"
          subtitle="点击上方按钮为角色生成日记（需角色已有记忆）"
        />
      )}

      {selectedCharacter &&
        !isLoading &&
        !error &&
        diaries.length > 0 &&
        filteredDiaries.length === 0 && (
          <EmptyState
            icon="🔍"
            title="未匹配到日记"
            subtitle="尝试更换搜索关键词"
          />
        )}

      {/* 日记列表 */}
      {filteredDiaries.length > 0 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="space-y-4"
        >
          {filteredDiaries.map((diary: DiaryEntry, idx: number) => {
            const periodInfo = getPeriodInfo(diary.period);
            return (
              <motion.div key={diary.id || idx} variants={item}>
                <GlassCard className="space-y-3" hover>
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${periodInfo.color}`}
                      >
                        {periodInfo.emoji} {periodInfo.label}
                      </span>
                      {diary.mood && (
                        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200/50">
                          <Flame className="w-3 h-3" />
                          {diary.mood}
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-twilight-400 flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {formatDate(diary.diary_date)}
                    </span>
                  </div>

                  <h3 className="text-lg font-semibold gradient-text flex items-center gap-2">
                    <BookOpen className="w-4 h-4 text-sakura-400" />
                    {diary.title}
                  </h3>

                  <p className="text-sm text-twilight-700 leading-relaxed whitespace-pre-wrap">
                    {diary.content}
                  </p>

                  {diary.diary_end_date && (
                    <div className="text-xs text-twilight-400 flex items-center gap-1 pt-1 border-t border-sakura-200/30">
                      <Calendar className="w-3 h-3" />
                      记录区间：{formatDate(diary.diary_end_date)} ~{" "}
                      {formatDate(diary.diary_date)}
                    </div>
                  )}
                </GlassCard>
              </motion.div>
            );
          })}
        </motion.div>
      )}

      {/* 说明 */}
      {selectedCharacter && !isLoading && diaries.length > 0 && (
        <GlassCard hover={false}>
          <div className="flex items-start gap-3 text-sm text-twilight-400">
            <BookOpen className="w-5 h-5 text-sakura-400 mt-0.5 shrink-0" />
            <div>
              <div className="font-medium text-twilight-500 mb-1">日记说明</div>
              <ul className="space-y-1">
                <li>
                  • 日记基于角色记忆（memory_episodes）由 LLM 生成叙事性归档
                </li>
                <li>• 不替代 Episode 真相源，是角色视角的二次叙事</li>
                <li>• 至少需要 3 条记忆才能生成日记</li>
                <li>• 生成日记会消耗 LLM 调用配额</li>
              </ul>
            </div>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
