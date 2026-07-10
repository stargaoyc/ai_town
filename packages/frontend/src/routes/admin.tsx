import { createFileRoute } from '@tanstack/react-router';
import { GlassCard, LoadingSpinner, ErrorDisplay, StatusBadge, StatCard } from '@/components/ui';
import { useAdminStatus, useForceTick } from '@/lib/queries';

export const Route = createFileRoute('/admin')({
  component: AdminPage,
});

function AdminPage() {
  const { data: status, isLoading, error } = useAdminStatus();
  const forceTick = useForceTick();

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold text-sakura-600">系统管理</h2>

      {isLoading && <LoadingSpinner />}
      {error && <ErrorDisplay error={error} />}

      {status && (
        <>
          <GlassCard>
            <h3 className="font-semibold text-sakura-600 mb-4">系统状态</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-4 rounded-xl bg-white/30">
                <div className="text-sm text-twilight-400">World Engine</div>
                <div className="mt-1">
                  <StatusBadge
                    status={status.world_engine === 'running' ? 'ok' : 'error'}
                    label={status.world_engine}
                  />
                </div>
              </div>
              <div className="p-4 rounded-xl bg-white/30">
                <div className="text-sm text-twilight-400">Character Engine</div>
                <div className="mt-1">
                  <StatusBadge
                    status={status.character_engine === 'available' ? 'ok' : 'idle'}
                    label={status.character_engine}
                  />
                </div>
              </div>
              <div className="p-4 rounded-xl bg-white/30">
                <div className="text-sm text-twilight-400">Redis</div>
                <div className="mt-1">
                  <StatusBadge
                    status={status.redis === 'connected' ? 'ok' : 'error'}
                    label={status.redis}
                  />
                </div>
              </div>
              <StatCard title="Tick ID" value={`#${status.tick_id}`} icon="⏱️" />
            </div>
          </GlassCard>

          <GlassCard>
            <h3 className="font-semibold text-sakura-600 mb-4">运维操作</h3>
            <div className="flex gap-4">
              <button
                onClick={() => forceTick.mutate()}
                disabled={forceTick.isPending}
                className="px-4 py-2 rounded-lg bg-sakura-400 text-white text-sm hover:bg-sakura-500 disabled:opacity-50 transition-colors"
              >
                {forceTick.isPending ? '执行中...' : '强制 Tick'}
              </button>
              {forceTick.isSuccess && (
                <span className="text-sm text-emerald-600 self-center">Tick 已触发</span>
              )}
              {forceTick.isError && (
                <span className="text-sm text-red-600 self-center">失败: {forceTick.error.message}</span>
              )}
            </div>
          </GlassCard>
        </>
      )}
    </div>
  );
}
