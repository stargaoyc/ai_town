import { createFileRoute, Link } from '@tanstack/react-router';
import { GlassCard, StatCard, ErrorDisplay, StatusBadge, PageHeader, SkeletonList } from '@/components/ui';
import { useHealth, useWorld, useCharacters } from '@/lib/queries';

export const Route = createFileRoute('/')({
  component: HomePage,
});

function HomePage() {
  const health = useHealth();
  const world = useWorld();
  const characters = useCharacters({ active_only: true });

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader title="Dashboard" subtitle="二次元 AI 小镇陪伴智能体" icon="🌸" />

      <GlassCard>
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div>
            <h2 className="text-xl font-semibold text-twilight-500">系统状态</h2>
            <p className="text-sm text-twilight-400 mt-1">实时监控小镇运行状况</p>
          </div>
          <StatusBadge
            status={health.data?.status === 'ok' ? 'ok' : 'error'}
            label={health.data?.status === 'ok' ? '🟢 运行中' : '🔴 异常'}
          />
        </div>
      </GlassCard>

      {health.isLoading && <SkeletonList count={1} />}
      {health.error && <ErrorDisplay error={health.error} />}

      {health.data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard title="World Tick" value={`#${health.data.world_tick}`} icon="⏱️" color="sakura" />
          <StatCard title="Redis" value={health.data.redis === 'connected' ? '✅ 已连接' : '❌ 断开'} icon="🔴" color="sky" />
          <StatCard title="天气" value={world.data?.weather ?? '—'} icon="🌤️" color="twilight" />
          <StatCard title="活跃角色" value={characters.data?.total ?? 0} icon="👥" color="sakura" />
        </div>
      )}

      <div className="grid md:grid-cols-3 gap-4">
        <Link to="/characters">
          <GlassCard className="hover:scale-[1.02] transition-transform cursor-pointer h-full">
            <div className="text-4xl mb-3">👥</div>
            <h3 className="font-semibold text-sakura-600 text-lg">角色管理</h3>
            <p className="text-sm text-twilight-400 mt-1">查看角色状态、对话与记忆</p>
            <div className="mt-3 text-sakura-400 text-sm">查看详情 →</div>
          </GlassCard>
        </Link>
        <Link to="/world">
          <GlassCard className="hover:scale-[1.02] transition-transform cursor-pointer h-full">
            <div className="text-4xl mb-3">🌍</div>
            <h3 className="font-semibold text-sky-soft-500 text-lg">世界状态</h3>
            <p className="text-sm text-twilight-400 mt-1">虚拟时间、天气与事件</p>
            <div className="mt-3 text-sky-soft-400 text-sm">查看详情 →</div>
          </GlassCard>
        </Link>
        <Link to="/map">
          <GlassCard className="hover:scale-[1.02] transition-transform cursor-pointer h-full">
            <div className="text-4xl mb-3">🗺️</div>
            <h3 className="font-semibold text-twilight-500 text-lg">小镇地图</h3>
            <p className="text-sm text-twilight-400 mt-1">场景热力图与角色分布</p>
            <div className="mt-3 text-twilight-400 text-sm">查看详情 →</div>
          </GlassCard>
        </Link>
      </div>
    </div>
  );
}
