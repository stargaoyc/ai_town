import { createFileRoute } from '@tanstack/react-router';
import { GlassCard, ErrorDisplay, ProgressBar, PageHeader, SkeletonList, EmptyState } from '@/components/ui';
import { useScenes } from '@/lib/queries';

export const Route = createFileRoute('/map')({
  component: MapPage,
});

function MapPage() {
  const { data, isLoading, error } = useScenes();

  const getCrowdednessColor = (c?: number) => {
    if (!c || c <= 0.3) return 'bg-emerald-100 text-emerald-700 border border-emerald-200/50';
    if (c <= 0.7) return 'bg-amber-100 text-amber-700 border border-amber-200/50';
    return 'bg-red-100 text-red-600 border border-red-200/50';
  };

  const getCrowdednessEmoji = (c?: number) => {
    if (!c || c <= 0.3) return '🟢';
    if (c <= 0.7) return '🟡';
    return '🔴';
  };

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader title="小镇地图" subtitle="场景与拥挤度一览" icon="🗺️" />

      {isLoading && <SkeletonList count={4} />}
      {error && <ErrorDisplay error={error} />}
      {data && data.data.length === 0 && <EmptyState icon="🏝️" title="暂无场景数据" />}

      {data && data.data.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {data.data.map((scene) => (
            <GlassCard key={scene.id} className="space-y-2 hover:scale-[1.03] transition-transform">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-sakura-600">{scene.name}</h3>
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${getCrowdednessColor(scene.crowdedness)}`}>
                  {getCrowdednessEmoji(scene.crowdedness)} {scene.crowdedness != null ? `${Math.round(scene.crowdedness * 100)}%` : '—'}
                </span>
              </div>
              {scene.description && (
                <p className="text-xs text-twilight-400 line-clamp-2">{scene.description}</p>
              )}
              <div className="text-xs text-twilight-400 flex items-center gap-2">
                {scene.type && <span className="px-1.5 py-0.5 rounded bg-twilight-100 text-twilight-500">{scene.type}</span>}
                {scene.capacity && <span>容量 {scene.capacity}</span>}
              </div>
              {scene.crowdedness != null && (
                <ProgressBar value={scene.crowdedness * 100} color={scene.crowdedness > 0.7 ? 'twilight' : 'sakura'} />
              )}
              {scene.characters_present && scene.characters_present.length > 0 && (
                <div className="text-xs text-twilight-400 flex items-center gap-1">
                  <span>👥</span> 在场 {scene.characters_present.length} 人
                </div>
              )}
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  );
}
