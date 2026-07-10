import { createFileRoute } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { Users } from "lucide-react";
import {
  GlassCard,
  ErrorDisplay,
  ProgressBar,
  PageHeader,
  SkeletonList,
  EmptyState,
} from "@/components/ui";
import { useScenes } from "@/lib/queries";

export const Route = createFileRoute("/map")({
  component: MapPage,
});

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

function MapPage() {
  const { data, isLoading, error } = useScenes();

  const getCrowdednessColor = (c?: number) => {
    if (!c || c <= 0.3)
      return "bg-emerald-100/80 text-emerald-700 border border-emerald-200/60";
    if (c <= 0.7)
      return "bg-amber-100/80 text-amber-700 border border-amber-200/60";
    return "bg-red-100/80 text-red-600 border border-red-200/60";
  };

  const getCrowdednessEmoji = (c?: number) => {
    if (!c || c <= 0.3) return "🟢";
    if (c <= 0.7) return "🟡";
    return "🔴";
  };

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader title="小镇地图" subtitle="场景与拥挤度一览" icon="🗺️" />

      {isLoading && <SkeletonList count={4} />}
      {error && <ErrorDisplay error={error} />}
      {data && data.data.length === 0 && (
        <EmptyState icon="🏝️" title="暂无场景数据" />
      )}

      {data && data.data.length > 0 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4"
        >
          {data.data.map((scene) => (
            <motion.div key={scene.id} variants={item}>
              <GlassCard className="space-y-3 h-full">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-semibold text-sakura-600 truncate">
                    {scene.name}
                  </h3>
                  <span
                    className={`shrink-0 px-2 py-0.5 rounded-full text-xs font-medium ${getCrowdednessColor(
                      scene.crowdedness,
                    )}`}
                  >
                    {getCrowdednessEmoji(scene.crowdedness)}{" "}
                    {scene.crowdedness != null
                      ? `${Math.round(scene.crowdedness * 100)}%`
                      : "—"}
                  </span>
                </div>
                {scene.description && (
                  <p className="text-xs text-twilight-400 line-clamp-2">
                    {scene.description}
                  </p>
                )}
                <div className="text-xs text-twilight-400 flex items-center gap-2 flex-wrap">
                  {scene.type && (
                    <span className="px-1.5 py-0.5 rounded-lg bg-twilight-100 text-twilight-500">
                      {scene.type}
                    </span>
                  )}
                  {scene.capacity && <span>容量 {scene.capacity}</span>}
                </div>
                {scene.crowdedness != null && (
                  <ProgressBar
                    value={scene.crowdedness * 100}
                    color={scene.crowdedness > 0.7 ? "twilight" : "sakura"}
                  />
                )}
                {scene.characters_present &&
                  scene.characters_present.length > 0 && (
                    <div className="text-xs text-twilight-400 flex items-center gap-1 pt-1">
                      <Users className="w-3.5 h-3.5" />
                      在场 {scene.characters_present.length} 人
                    </div>
                  )}
              </GlassCard>
            </motion.div>
          ))}
        </motion.div>
      )}
    </div>
  );
}
