import { createFileRoute, Link } from '@tanstack/react-router';
import { LoadingSpinner, ErrorDisplay, StatusBadge } from '@/components/ui';
import { useCharacters } from '@/lib/queries';

export const Route = createFileRoute('/characters')({
  component: CharactersPage,
});

function CharactersPage() {
  const { data, isLoading, error } = useCharacters();

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold text-sakura-600">角色列表</h2>
      {isLoading && <LoadingSpinner />}
      {error && <ErrorDisplay error={error} />}
      {data && (
        <div className="grid gap-4">
          {data.data.map((char) => (
            <Link key={char.id} to="/characters/$characterId" params={{ characterId: char.id }}>
              <div className="p-4 rounded-xl bg-white/30 flex items-center gap-4 hover:bg-white/50 transition-colors cursor-pointer">
                <div className="w-12 h-12 rounded-full bg-sakura-300 flex items-center justify-center text-sakura-600 font-bold text-lg">
                  {char.name[0]}
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-sakura-600">{char.name}</span>
                    <StatusBadge
                      status={char.is_active ? 'ok' : 'idle'}
                      label={char.is_active ? '活跃' : '休眠'}
                    />
                  </div>
                  <div className="text-sm text-twilight-400">
                    {char.age ? `${char.age}岁 · ` : ''}{char.occupation ?? '未知'}
                  </div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
