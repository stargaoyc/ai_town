import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import {
  MapPin,
  Activity,
  Battery,
  Utensils,
  Smile,
  Wallet,
  Users,
  X,
  Zap,
} from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  ErrorDisplay,
  EmptyState,
  StatusBadge,
  ProgressBar,
} from "@/components/ui";
import { useCharacters, useCharacter } from "@/lib/queries";
import type { Character } from "@/lib/api";

export const Route = createFileRoute("/compare")({
  component: ComparePage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.08 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

// 最大可选角色数
const MAX_SELECTED = 4;

// 状态维度配置：用于 ProgressBar 对比展示（数值型百分比状态）
const stateDimensions: {
  key: "stamina" | "satiety" | "phone_battery" | "social_energy";
  label: string;
  icon: typeof Activity;
  max: number;
  color: "sakura" | "sky" | "twilight";
}[] = [
  {
    key: "stamina",
    label: "精力",
    icon: Zap,
    max: 100,
    color: "sakura",
  },
  {
    key: "satiety",
    label: "饱腹",
    icon: Utensils,
    max: 100,
    color: "sky",
  },
  {
    key: "social_energy",
    label: "社交精力",
    icon: Users,
    max: 100,
    color: "twilight",
  },
  {
    key: "phone_battery",
    label: "手机电量",
    icon: Battery,
    max: 100,
    color: "sky",
  },
];

function ComparePage() {
  // 获取活跃角色列表（仅基本信息，用于选择）
  const {
    data: charactersData,
    isLoading,
    error,
  } = useCharacters({
    active_only: true,
  });
  const characters = charactersData?.data ?? [];

  // 已选角色 ID 列表（最多 4 个）
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  // 逐个获取已选角色详情（含 state 数据）
  const char1 = useCharacter(selectedIds[0] ?? "");
  const char2 = useCharacter(selectedIds[1] ?? "");
  const char3 = useCharacter(selectedIds[2] ?? "");
  const char4 = useCharacter(selectedIds[3] ?? "");

  // 已选角色详情（含 state）
  const selectedCharacters = useMemo(() => {
    const chars = [char1.data, char2.data, char3.data, char4.data];
    return chars.filter((c): c is Character => !!c);
  }, [char1.data, char2.data, char3.data, char4.data]);

  // 切换角色选中状态
  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) {
        // 已选则取消选择
        return prev.filter((x) => x !== id);
      }
      // 未选则添加（不超过上限）
      if (prev.length >= MAX_SELECTED) return prev;
      return [...prev, id];
    });
  };

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader
        title="多角色对比"
        subtitle="并排对比多个角色的基本信息、状态与行为"
        icon="🔄"
        backTo="/admin"
        backLabel="返回管理"
      />

      {/* 顶部统计 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          title="活跃角色"
          value={characters.length}
          icon="👥"
          color="sakura"
        />
        <StatCard
          title="已选对比"
          value={`${selectedIds.length} / ${MAX_SELECTED}`}
          icon="🔄"
          color="sky"
        />
        <StatCard
          title="可再选"
          value={Math.max(0, MAX_SELECTED - selectedIds.length)}
          icon="➕"
          color="twilight"
        />
      </div>

      {/* 角色选择区 */}
      <GlassCard hover={false}>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-twilight-500 font-medium">
              选择角色（最多 {MAX_SELECTED} 个）
            </span>
            {selectedIds.length > 0 && (
              <button
                onClick={() => setSelectedIds([])}
                className="text-xs text-twilight-400 hover:text-sakura-500 transition-colors"
              >
                清空选择
              </button>
            )}
          </div>
          {isLoading ? (
            <div className="text-sm text-twilight-400">加载角色中...</div>
          ) : characters.length === 0 ? (
            <div className="text-sm text-twilight-400">暂无活跃角色</div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {characters.map((char) => {
                const selected = selectedIds.includes(char.id);
                const disabled =
                  !selected && selectedIds.length >= MAX_SELECTED;
                return (
                  <motion.button
                    key={char.id}
                    {...(!disabled ? { whileHover: { scale: 1.05 } } : {})}
                    {...(!disabled ? { whileTap: { scale: 0.95 } } : {})}
                    onClick={() => !disabled && toggleSelect(char.id)}
                    disabled={disabled}
                    className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm font-medium border transition-all ${
                      selected
                        ? "bg-gradient-to-r from-sakura-400 to-sakura-500 text-white border-transparent shadow-md shadow-sakura-400/30"
                        : disabled
                          ? "bg-white/30 text-twilight-300 border-white/30 cursor-not-allowed"
                          : "bg-white/60 text-twilight-600 border-sakura-200/50 hover:border-sakura-300/50"
                    }`}
                  >
                    <span
                      className={`w-6 h-6 rounded-lg flex items-center justify-center text-xs font-bold ${
                        selected
                          ? "bg-white/30"
                          : "bg-gradient-to-br from-sakura-300 to-twilight-300 text-white"
                      }`}
                    >
                      {char.name[0]}
                    </span>
                    <span>{char.name}</span>
                    {selected && <X className="w-3.5 h-3.5" />}
                  </motion.button>
                );
              })}
            </div>
          )}
        </div>
      </GlassCard>

      {error && <ErrorDisplay error={error} />}

      {/* 未选择角色提示 */}
      {selectedIds.length < 2 && !isLoading && !error && (
        <EmptyState
          icon="🔄"
          title="请选择 2-4 个角色进行对比"
          subtitle="从上方角色列表中选择多个角色，即可并排查看对比信息"
        />
      )}

      {/* 对比卡片：并排展示 */}
      {selectedCharacters.length >= 2 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className={`grid gap-4 ${
            selectedCharacters.length === 2
              ? "md:grid-cols-2"
              : selectedCharacters.length === 3
                ? "md:grid-cols-3"
                : "md:grid-cols-2 lg:grid-cols-4"
          }`}
        >
          {selectedCharacters.map((char) => {
            const state = char.state;
            return (
              <motion.div key={char.id} variants={item}>
                <GlassCard className="space-y-4" hover>
                  {/* 头部：头像 + 名称 */}
                  <div className="flex items-center gap-3">
                    <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-sakura-300 via-sakura-400 to-twilight-300 flex items-center justify-center text-white font-bold text-lg shadow-lg shrink-0">
                      {char.name[0]}
                    </div>
                    <div className="min-w-0">
                      <div className="font-semibold text-sakura-600 truncate">
                        {char.name}
                      </div>
                      <div className="text-xs text-twilight-400">{char.id}</div>
                    </div>
                  </div>

                  {/* 基本信息对比 */}
                  <div className="space-y-1.5 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="text-twilight-400">职业</span>
                      <span className="text-twilight-600 font-medium">
                        {char.occupation ?? "—"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-twilight-400">年龄</span>
                      <span className="text-twilight-600 font-medium">
                        {char.age ? `${char.age} 岁` : "—"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-twilight-400">状态</span>
                      <StatusBadge
                        status={char.is_active ? "ok" : "idle"}
                        label={char.is_active ? "活跃" : "休眠"}
                      />
                    </div>
                  </div>

                  {/* 状态对比 - ProgressBar 展示 */}
                  {state ? (
                    <div className="space-y-2.5">
                      <div className="text-xs font-semibold text-twilight-500 uppercase tracking-wide">
                        状态
                      </div>
                      {stateDimensions.map((dim) => {
                        const Icon = dim.icon;
                        const val = (state[dim.key] as number) ?? 0;
                        return (
                          <div key={dim.key}>
                            <div className="flex items-center justify-between mb-1 text-xs">
                              <span className="text-twilight-400 flex items-center gap-1">
                                <Icon className="w-3 h-3" />
                                {dim.label}
                              </span>
                              <span className="text-twilight-500 font-semibold">
                                {Math.round(val)}%
                              </span>
                            </div>
                            <ProgressBar
                              value={val}
                              max={dim.max}
                              color={dim.color}
                            />
                          </div>
                        );
                      })}
                      {/* 情绪（字符串类型，直接展示文本） */}
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-twilight-400 flex items-center gap-1">
                          <Smile className="w-3 h-3" />
                          情绪
                        </span>
                        <span className="text-twilight-500 font-semibold">
                          {state.mood ?? "—"}
                        </span>
                      </div>
                      {/* 金钱（非百分比，直接展示数值） */}
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-twilight-400 flex items-center gap-1">
                          <Wallet className="w-3 h-3" />
                          金钱
                        </span>
                        <span className="text-twilight-500 font-semibold">
                          {state.money?.toLocaleString() ?? "—"}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-twilight-300 italic">
                      暂无状态数据
                    </div>
                  )}

                  {/* 当前位置对比 */}
                  <div className="space-y-1">
                    <div className="text-xs font-semibold text-twilight-500 uppercase tracking-wide flex items-center gap-1">
                      <MapPin className="w-3 h-3" />
                      当前位置
                    </div>
                    <div className="text-sm text-twilight-600 bg-white/40 rounded-lg px-2 py-1.5 border border-white/40">
                      {state?.location ?? "—"}
                    </div>
                  </div>

                  {/* 当前行为对比 */}
                  <div className="space-y-1">
                    <div className="text-xs font-semibold text-twilight-500 uppercase tracking-wide flex items-center gap-1">
                      <Activity className="w-3 h-3" />
                      当前行为
                    </div>
                    <div className="text-sm text-twilight-600 bg-white/40 rounded-lg px-2 py-1.5 border border-white/40 break-words">
                      {state?.current_action
                        ? typeof state.current_action === "object"
                          ? String(
                              (state.current_action as Record<string, unknown>)
                                .action_name ??
                                (
                                  state.current_action as Record<
                                    string,
                                    unknown
                                  >
                                ).action_id ??
                                "无",
                            )
                          : JSON.stringify(state.current_action)
                        : "无"}
                    </div>
                  </div>
                </GlassCard>
              </motion.div>
            );
          })}
        </motion.div>
      )}
    </div>
  );
}
