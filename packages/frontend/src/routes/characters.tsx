import { createFileRoute, Link } from "@tanstack/react-router";
import { motion } from "framer-motion";
import {
  ErrorDisplay,
  StatusBadge,
  PageHeader,
  SkeletonList,
  EmptyState,
  GlassCard,
} from "@/components/ui";
import { useCharacters } from "@/lib/queries";

export const Route = createFileRoute("/characters")({
  component: CharactersPage,
});

const container = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.06 },
  },
};

const cardItem = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

function CharactersPage() {
  const { data, isLoading, error } = useCharacters();

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader title="角色列表" subtitle="小镇中的所有角色" icon="👥" />

      {isLoading && <SkeletonList count={4} />}
      {error && <ErrorDisplay error={error} />}
      {data && data.data.length === 0 && (
        <EmptyState
          icon="👻"
          title="还没有角色"
          subtitle="导入角色卡后将显示在这里"
        />
      )}

      {data && data.data.length > 0 && (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="grid md:grid-cols-2 gap-4"
        >
          {data.data.map((char) => (
            <motion.div key={char.id} variants={cardItem}>
              <Link
                to="/characters/$characterId"
                params={{ characterId: char.id }}
              >
                <GlassCard className="!p-0 cursor-pointer group" hover>
                  <div className="p-5 flex items-center gap-4">
                    <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-sakura-300 via-sakura-400 to-twilight-300 flex items-center justify-center text-white font-bold text-xl shadow-lg group-hover:scale-110 group-hover:rotate-3 transition-transform">
                      {char.name[0]}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-semibold text-sakura-600 text-lg truncate">
                          {char.name}
                        </span>
                        <StatusBadge
                          status={char.is_active ? "ok" : "idle"}
                          label={char.is_active ? "活跃" : "休眠"}
                        />
                      </div>
                      <div className="text-sm text-twilight-400">
                        {char.age ? `${char.age}岁 · ` : ""}
                        {char.occupation ?? "未知职业"}
                      </div>
                      {char.backstory && (
                        <div className="text-xs text-twilight-300 mt-1 line-clamp-1">
                          {char.backstory}
                        </div>
                      )}
                    </div>
                    <div className="w-8 h-8 rounded-full bg-white/50 flex items-center justify-center text-sakura-400 group-hover:bg-sakura-400 group-hover:text-white transition-colors">
                      →
                    </div>
                  </div>
                </GlassCard>
              </Link>
            </motion.div>
          ))}
        </motion.div>
      )}
    </div>
  );
}
