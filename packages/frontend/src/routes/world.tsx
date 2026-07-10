import { createFileRoute } from '@tanstack/react-router';
import { GlassCard, LoadingSpinner, ErrorDisplay, StatCard } from '@/components/ui';
import { useWorld, useActions } from '@/lib/queries';

export const Route = createFileRoute('/world')({
  component: WorldPage,
});

function WorldPage() {
  const { data: world, isLoading, error } = useWorld();
  const { data: actionsData } = useActions();

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold text-sakura-600">世界状态</h2>

      {isLoading && <LoadingSpinner />}
      {error && <ErrorDisplay error={error} />}

      {world && (
        <GlassCard>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard title="虚拟时间" value={world.world_time} icon="🕐" />
            <StatCard title="天气" value={world.weather} icon="🌤️" color="twilight" />
            <StatCard title="World Tick" value={`#${world.tick_id}`} icon="⏱️" color="sky" />
            <StatCard title="活跃角色" value={world.active_characters} icon="👥" />
          </div>
        </GlassCard>
      )}

      <GlassCard>
        <h3 className="font-semibold text-sakura-600 mb-4">可用 Action 列表</h3>
        <div className="grid md:grid-cols-2 gap-3">
          {actionsData?.data?.map((action) => (
            <div key={action.id} className="p-3 rounded-lg bg-white/30">
              <div className="font-medium text-sakura-600">{action.name}</div>
              <div className="text-xs text-twilight-400 mt-1">
                {action.category} · {action.id}
              </div>
              {action.description && (
                <p className="text-sm text-twilight-500 mt-1">{action.description}</p>
              )}
            </div>
          )) ?? <p className="text-sm text-twilight-400">暂无 Action</p>}
        </div>
      </GlassCard>
    </div>
  );
}
