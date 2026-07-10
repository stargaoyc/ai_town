import { createFileRoute } from '@tanstack/react-router';
import { GlassCard, LoadingSpinner, ErrorDisplay, ProgressBar } from '@/components/ui';
import { useScenes } from '@/lib/queries';

export const Route = createFileRoute('/map')({
  component: MapPage,
});

function MapPage() {
  const { data, isLoading, error } = useScenes();

  const getCrowdednessColor = (c?: number) => {
    if (!c || c <= 0.3) return 'bg-emerald-100 text-emerald-700';
    if (c <= 0.7) return 'bg-amber-100 text-amber-700';
    return 'bg-red-100 text-red-700';
  };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold text-sakura-600">小镇地图</h2>

      {isLoading && <LoadingSpinner />}
      {error && <ErrorDisplay error={error} />}

      {data && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {data.data.map((scene) => (
            <GlassCard key={scene.id} className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-sakura-600">{scene.name}</h3>
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${getCrowdednessColor(scene.crowdedness)}`}>
                  {scene.crowdedness != null ? `${Math.round(scene.crowdedness * 100)}%` : '—'}
                </span>
              </div>
              {scene.description && (
                <p className="text-xs text-twilight-400">{scene.description}</p>
              )}
              <div className="text-xs text-twilight-400">
                {scene.type && <span>{scene.type}</span>}
                {scene.capacity && <span> · 容量 {scene.capacity}</span>}
              </div>
              {scene.crowdedness != null && (
                <ProgressBar value={scene.crowdedness * 100} color={scene.crowdedness > 0.7 ? 'twilight' : 'sakura'} />
              )}
              {scene.characters_present && scene.characters_present.length > 0 && (
                <div className="text-xs text-twilight-400">
                  在场: {scene.characters_present.length} 人
                </div>
              )}
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  );
}
