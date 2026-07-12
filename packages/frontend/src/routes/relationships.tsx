import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { Users, Heart, Shield, Link2 } from "lucide-react";
import {
  GlassCard,
  PageHeader,
  StatCard,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  ProgressBar,
} from "@/components/ui";
import { useCharacters, useRelations } from "@/lib/queries";
import type { RelationEntry } from "@/lib/api";

export const Route = createFileRoute("/relationships")({
  component: RelationshipsPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

// 根据信任度返回连线颜色（高信任=绿，中=琥珀，低=红）
function trustColor(trust: number): string {
  if (trust >= 60) return "#34d399"; // emerald-400
  if (trust >= 30) return "#fbbf24"; // amber-400
  return "#f87171"; // red-400
}

// 根据亲密度返回连线粗细
function intimacyWidth(intimacy: number): number {
  return Math.max(1, Math.min(8, 1 + (intimacy / 100) * 7));
}

// 关系类型对应的标签样式
const relationTypeColors: Record<string, string> = {
  friend: "bg-sakura-100 text-sakura-600 border-sakura-200/50",
  close_friend: "bg-sakura-100 text-sakura-600 border-sakura-200/50",
  best_friend: "bg-sakura-100 text-sakura-600 border-sakura-200/50",
  family: "bg-twilight-100 text-twilight-500 border-twilight-200/50",
  colleague: "bg-sky-soft-100 text-sky-soft-600 border-sky-soft-200/50",
  acquaintance: "bg-gray-100 text-gray-500 border-gray-200/50",
  stranger: "bg-gray-100 text-gray-500 border-gray-200/50",
  rival: "bg-red-100 text-red-500 border-red-200/50",
};

function relationLabel(type: string): string {
  const map: Record<string, string> = {
    friend: "朋友",
    close_friend: "密友",
    best_friend: "挚友",
    family: "家人",
    colleague: "同事",
    acquaintance: "熟人",
    stranger: "陌生人",
    rival: "对手",
    lover: "恋人",
  };
  return map[type] ?? type;
}

function RelationshipsPage() {
  const [selectedCharacter, setSelectedCharacter] = useState("");

  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  const { data: relationsData, isLoading, error } =
    useRelations(selectedCharacter);
  const relations = relationsData?.data ?? [];

  // 当前角色名（用于中心节点）
  const currentChar = characters.find((c) => c.id === selectedCharacter);
  const centerName = currentChar?.name ?? selectedCharacter ?? "?";

  // 力导向图节点位置：中心节点居中，关联节点环形分布
  const graph = useMemo(() => {
    const center = { x: 200, y: 200 };
    const radius = 135;
    const nodes = relations.map((rel, i) => {
      const angle = (2 * Math.PI * i) / Math.max(1, relations.length) - Math.PI / 2;
      return {
        rel,
        x: center.x + radius * Math.cos(angle),
        y: center.y + radius * Math.sin(angle),
      };
    });
    return { center, nodes };
  }, [relations]);

  // 平均信任度 / 亲密度（后端使用 strength 字段，映射为 trust 和 intimacy）
  const avg = useMemo(() => {
    if (relations.length === 0) return { trust: 0, intimacy: 0 };
    const t = relations.reduce((s, r) => s + (r.strength ?? r.trust ?? 0), 0) / relations.length;
    const i = relations.reduce((s, r) => s + (r.strength ?? r.intimacy ?? 0), 0) / relations.length;
    return { trust: Math.round(t), intimacy: Math.round(i) };
  }, [relations]);

  return (
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="关系图谱"
          subtitle="角色社交网络与亲密度可视化"
          icon="🔗"
        />

        {/* 顶部统计 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <StatCard
            title="关系总数"
            value={relations.length}
            icon="🔗"
            color="sakura"
          />
          <StatCard
            title="平均信任度"
            value={avg.trust}
            icon="🛡️"
            color="sky"
          />
          <StatCard
            title="平均亲密度"
            value={avg.intimacy}
            icon="💗"
            color="twilight"
          />
        </div>

        {/* 角色选择器 */}
        <GlassCard hover={false}>
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
        </GlassCard>

        {!selectedCharacter && (
          <EmptyState
            icon="👆"
            title="请先选择一个角色"
            subtitle="选择角色后将展示其关系图谱"
          />
        )}

        {selectedCharacter && isLoading && (
          <LoadingSpinner text="正在加载关系图谱..." />
        )}
        {selectedCharacter && !isLoading && error && (
          <ErrorDisplay error={error} />
        )}

        {selectedCharacter &&
          !isLoading &&
          !error &&
          relations.length === 0 && (
            <EmptyState
              icon="🌱"
              title="暂无关系记录"
              subtitle="该角色还没有建立任何社交关系"
            />
          )}

        {/* 关系图谱 + 详情列表 */}
        {relations.length > 0 && (
          <div className="grid lg:grid-cols-2 gap-4">
            {/* SVG 力导向图 */}
            <motion.div variants={item} initial="hidden" animate="show">
              <GlassCard hover={false} className="h-full">
                <h3 className="font-semibold text-sakura-600 mb-3 flex items-center gap-2">
                  <Link2 className="w-4 h-4" />
                  关系网络图
                </h3>
                <div className="flex justify-center">
                  <svg
                    viewBox="0 0 400 400"
                    className="w-full max-w-md h-auto"
                  >
                    {/* 连线 */}
                    {graph.nodes.map((node, i) => {
                      const strength = node.rel.strength ?? node.rel.trust ?? 0;
                      const color = trustColor(strength);
                      const width = intimacyWidth(strength);
                      return (
                        <line
                          key={`line-${i}`}
                          x1={graph.center.x}
                          y1={graph.center.y}
                          x2={node.x}
                          y2={node.y}
                          stroke={color}
                          strokeWidth={width}
                          strokeOpacity={0.6}
                          strokeLinecap="round"
                        />
                      );
                    })}

                    {/* 关联节点 */}
                    {graph.nodes.map((node, i) => {
                      const strength = node.rel.strength ?? node.rel.trust ?? 0;
                      const color = trustColor(strength);
                      const label =
                        node.rel.target_name ?? node.rel.target_id ?? "?";
                      return (
                        <g key={`node-${i}`}>
                          <circle
                            cx={node.x}
                            cy={node.y}
                            r={18}
                            fill="rgba(255,255,255,0.85)"
                            stroke={color}
                            strokeWidth={2.5}
                          />
                          <text
                            x={node.x}
                            y={node.y + 4}
                            textAnchor="middle"
                            className="fill-twilight-600 text-[10px] font-bold"
                          >
                            {label.slice(0, 2)}
                          </text>
                        </g>
                      );
                    })}

                    {/* 中心节点 */}
                    <circle
                      cx={graph.center.x}
                      cy={graph.center.y}
                      r={26}
                      fill="url(#centerGrad)"
                      stroke="#ff7a94"
                      strokeWidth={3}
                    />
                    <defs>
                      <radialGradient id="centerGrad">
                        <stop offset="0%" stopColor="#ffd6e0" />
                        <stop offset="100%" stopColor="#ff7a94" />
                      </radialGradient>
                    </defs>
                    <text
                      x={graph.center.x}
                      y={graph.center.y + 4}
                      textAnchor="middle"
                      className="fill-white text-xs font-bold"
                    >
                      {centerName.slice(0, 3)}
                    </text>
                  </svg>
                </div>
                {/* 图例 */}
                <div className="flex flex-wrap items-center justify-center gap-4 mt-3 text-xs text-twilight-400">
                  <span className="flex items-center gap-1">
                    <span
                      className="inline-block w-4 h-0.5"
                      style={{ background: "#34d399" }}
                    />
                    高信任
                  </span>
                  <span className="flex items-center gap-1">
                    <span
                      className="inline-block w-4 h-0.5"
                      style={{ background: "#fbbf24" }}
                    />
                    中信任
                  </span>
                  <span className="flex items-center gap-1">
                    <span
                      className="inline-block w-4 h-0.5"
                      style={{ background: "#f87171" }}
                    />
                    低信任
                  </span>
                  <span className="text-twilight-300">| 线条越粗亲密度越高</span>
                </div>
              </GlassCard>
            </motion.div>

            {/* 关系详情列表 */}
            <motion.div
              variants={container}
              initial="hidden"
              animate="show"
              className="space-y-3"
            >
              {relations.map((rel: RelationEntry, i) => {
                const relType = rel.relationship_type ?? rel.relation_type ?? "stranger";
                const typeColor =
                  relationTypeColors[relType] ??
                  "bg-gray-100 text-gray-500 border-gray-200/50";
                const strength = rel.strength ?? rel.trust ?? 0;
                return (
                  <motion.div key={`${rel.target_id}-${i}`} variants={item}>
                    <GlassCard className="space-y-3" hover>
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <div className="flex items-center gap-2">
                          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-sakura-200 to-twilight-200 flex items-center justify-center text-white font-bold text-sm">
                            {(rel.target_name ?? rel.target_id ?? "?").slice(0, 1)}
                          </div>
                          <div>
                            <div className="font-semibold text-twilight-600 text-sm">
                              {rel.target_name ?? rel.target_id}
                            </div>
                            <span
                              className={`inline-block mt-0.5 px-2 py-0.5 rounded-full text-xs font-medium border ${typeColor}`}
                            >
                              {relationLabel(relType)}
                            </span>
                          </div>
                        </div>
                      </div>

                      {/* 关系强度 */}
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-twilight-400 flex items-center gap-1">
                            <Shield className="w-3 h-3" />
                            关系强度
                          </span>
                          <span className="font-semibold text-sky-soft-600">
                            {strength}
                          </span>
                        </div>
                        <ProgressBar
                          value={strength}
                          max={100}
                          color="sky"
                        />
                      </div>

                      {/* 亲密度（使用 strength 作为近似） */}
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-twilight-400 flex items-center gap-1">
                            <Heart className="w-3 h-3" />
                            亲密度
                          </span>
                          <span className="font-semibold text-sakura-600">
                            {rel.intimacy ?? strength}
                          </span>
                        </div>
                        <ProgressBar
                          value={rel.intimacy ?? strength}
                          max={100}
                          color="sakura"
                        />
                      </div>
                    </GlassCard>
                  </motion.div>
                );
              })}
            </motion.div>
          </div>
        )}

        {/* 说明 */}
        {selectedCharacter && !isLoading && relations.length > 0 && (
          <GlassCard hover={false}>
            <div className="flex items-start gap-3 text-sm text-twilight-400">
              <Users className="w-5 h-5 text-sakura-400 mt-0.5 shrink-0" />
              <div>
                <div className="font-medium text-twilight-500 mb-1">
                  图谱说明
                </div>
                <ul className="space-y-1">
                  <li>• 中心节点为当前角色，周围节点为关联角色</li>
                  <li>• 连线颜色表示信任度（绿高 / 琥珀中 / 红低）</li>
                  <li>• 连线粗细表示亲密度，越粗代表越亲密</li>
                </ul>
              </div>
            </div>
          </GlassCard>
        )}
      </div>
  );
}
