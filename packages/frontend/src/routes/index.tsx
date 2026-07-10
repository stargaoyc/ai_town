import { createFileRoute, Link } from '@tanstack/react-router';
import { GlassCard, StatCard, LoadingSpinner, ErrorDisplay, StatusBadge } from '@/components/ui';
import { useHealth, useWorld, useCharacters } from '@/lib/queries';

export const Route = createFileRoute('/')({
  component: HomePage,
});

function HomePage() {
  const health = useHealth();
  const world = useWorld();
  const characters = useCharacters({ active_only: true });

  return (
    <div className="space-y-6">
      <GlassCard>
        <h1 className="text-3xl font-bold text-sakura-600 mb-2">AI Town Dashboard</h1>
        <p className="text-twilight-500">二次元 AI 小镇陪伴智能体</p>
        <div className="mt-2">
          <StatusBadge
            status={health.data?.status === 'ok' ? 'ok' : 'error'}
            label={health.data?.status === 'ok' ? '运行中' : '异常'}
          />
        </div>
      </GlassCard>

      {health.isLoading && <LoadingSpinner />}
      {health.error && <ErrorDisplay error={health.error} />}

      {health.data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard title="World Tick" value={`#${health.data.world_tick}`} icon="⏱️" color="sakura" />
          <StatCard title="Redis" value={health.data.redis === 'connected' ? '已连接' : '断开'} icon="🔴" color="sky" />
          <StatCard title="天气" value={world.data?.weather ?? '—'} icon="🌤️" color="twilight" />
          <StatCard title="活跃角色" value={characters.data?.total ?? 0} icon="👥" color="sakura" />
        </div>
      )}

      <div className="grid md:grid-cols-3 gap-4">
        <Link to="/characters">
          <GlassCard className="hover:bg-sakura-50 transition-colors cursor-pointer">
            <div className="text-4xl mb-2">👥</div>
            <h3 className="font-semibold text-sakura-600">角色管理</h3>
            <p className="text-sm text-twilight-400 mt-1">查看角色状态与详情</p>
          </GlassCard>
        </Link>
        <Link to="/world">
          <GlassCard className="hover:bg-sky-soft-50 transition-colors cursor-pointer">
            <div className="text-4xl mb-2">🌍</div>
            <h3 className="font-semibold text-sky-soft-500">世界状态</h3>
            <p className="text-sm text-twilight-400 mt-1">时间/天气/事件</p>
          </GlassCard>
        </Link>
        <Link to="/map">
          <GlassCard className="hover:bg-twilight-50 transition-colors cursor-pointer">
            <div className="text-4xl mb-2">🗺️</div>
            <h3 className="font-semibold text-twilight-500">小镇地图</h3>
            <p className="text-sm text-twilight-400 mt-1">场景热力图</p>
          </GlassCard>
        </Link>
      </div>
    </div>
  );
}
