import { createFileRoute } from '@tanstack/react-router';
import { GlassCard, ErrorDisplay, StatCard, PageHeader, SkeletonList, EmptyState } from '@/components/ui';
import { useWorld, useActions } from '@/lib/queries';

export const Route = createFileRoute('/world')({
  component: WorldPage,
});

function WorldPage() {
  const { data: world, isLoading, error } = useWorld();
  const { data: actionsData } = useActions();

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader title="世界状态" subtitle="虚拟时间、天气与可用行为" icon="🌍" />

      {isLoading && <SkeletonList count={1} />}
      {error && <ErrorDisplay error={error} />}

      {world && (
        <GlassCard>
          <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2">
            <span>🌐</span> 当前状态
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard title="虚拟时间" value={world.world_time} icon="🕐" />
            <StatCard title="天气" value={world.weather} icon="🌤️" color="twilight" />
            <StatCard title="World Tick" value={`#${world.tick_id}`} icon="⏱️" color="sky" />
            <StatCard title="活跃角色" value={world.active_characters} icon="👥" />
          </div>
        </GlassCard>
      )}

      <GlassCard>
        <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2">
          <span>🎯</span> 可用 Action
        </h3>
        {actionsData?.data?.length === 0 && <EmptyState icon="🎯" title="暂无 Action" />}
        <div className="grid md:grid-cols-2 gap-3">
          {actionsData?.data?.map((action) => (
            <div key={action.id} className="p-4 rounded-xl bg-white/30 border border-white/20 hover:bg-white/40 hover:scale-[1.02] transition-all">
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-sakura-600">{action.name}</span>
                <span className="px-2 py-0.5 rounded-full text-xs bg-twilight-100 text-twilight-500">{action.category}</span>
              </div>
              {action.description && (
                <p className="text-sm text-twilight-400 mt-1">{action.description}</p>
              )}
            </div>
          )) ?? <p className="text-sm text-twilight-400">加载中...</p>}
        </div>
      </GlassCard>
    </div>
  );
}
