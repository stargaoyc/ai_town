import { createFileRoute, Link } from '@tanstack/react-router';
import { ErrorDisplay, StatusBadge, PageHeader, SkeletonList, EmptyState } from '@/components/ui';
import { useCharacters } from '@/lib/queries';

export const Route = createFileRoute('/characters')({
  component: CharactersPage,
});

function CharactersPage() {
  const { data, isLoading, error } = useCharacters();

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader title="角色列表" subtitle="小镇中的所有角色" icon="👥" />

      {isLoading && <SkeletonList count={4} />}
      {error && <ErrorDisplay error={error} />}
      {data && data.data.length === 0 && <EmptyState icon="👻" title="还没有角色" subtitle="导入角色卡后将显示在这里" />}

      {data && data.data.length > 0 && (
        <div className="grid md:grid-cols-2 gap-4">
          {data.data.map((char) => (
            <Link key={char.id} to="/characters/$characterId" params={{ characterId: char.id }}>
              <div className="p-5 rounded-2xl bg-glass-bg backdrop-blur-glass-blur border border-white/40 flex items-center gap-4 hover:shadow-glow hover:scale-[1.02] transition-all cursor-pointer">
                <div className="w-14 h-14 rounded-full bg-gradient-to-br from-sakura-300 to-twilight-300 flex items-center justify-center text-white font-bold text-xl shadow-lg">
                  {char.name[0]}
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-semibold text-sakura-600 text-lg">{char.name}</span>
                    <StatusBadge
                      status={char.is_active ? 'ok' : 'idle'}
                      label={char.is_active ? '活跃' : '休眠'}
                    />
                  </div>
                  <div className="text-sm text-twilight-400">
                    {char.age ? `${char.age}岁 · ` : ''}{char.occupation ?? '未知职业'}
                  </div>
                  {char.backstory && (
                    <div className="text-xs text-twilight-300 mt-1 line-clamp-1">{char.backstory}</div>
                  )}
                </div>
                <div className="text-sakura-400 text-sm">→</div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
