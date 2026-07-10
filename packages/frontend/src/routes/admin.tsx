import { createFileRoute } from '@tanstack/react-router';
import { GlassCard, ErrorDisplay, StatusBadge, StatCard, PageHeader, SkeletonList } from '@/components/ui';
import { useAdminStatus, useForceTick } from '@/lib/queries';

export const Route = createFileRoute('/admin')({
  component: AdminPage,
});

function AdminPage() {
  const { data: status, isLoading, error } = useAdminStatus();
  const forceTick = useForceTick();

  return (
    <div className="space-y-6 animate-fade-in-up">
      <PageHeader title="系统管理" subtitle="运维操作与状态监控" icon="⚙️" />

      {isLoading && <SkeletonList count={2} />}
      {error && <ErrorDisplay error={error} />}

      {status && (
        <>
          <GlassCard>
            <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2">
              <span>📊</span> 系统状态
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-4 rounded-xl bg-gradient-to-br from-white/40 to-sakura-50/30 border border-white/20">
                <div className="text-sm text-twilight-400 mb-2">World Engine</div>
                <StatusBadge
                  status={status.world_engine.running ? 'ok' : 'error'}
                  label={status.world_engine.running ? '运行中' : '停止'}
                />
              </div>
              <div className="p-4 rounded-xl bg-gradient-to-br from-white/40 to-sky-soft-50/30 border border-white/20">
                <div className="text-sm text-twilight-400 mb-2">Character Engine</div>
                <StatusBadge
                  status={status.character_engine.available ? 'ok' : 'idle'}
                  label={status.character_engine.available ? '可用' : '未启动'}
                />
              </div>
              <div className="p-4 rounded-xl bg-gradient-to-br from-white/40 to-twilight-50/30 border border-white/20">
                <div className="text-sm text-twilight-400 mb-2">Redis</div>
                <StatusBadge
                  status={status.redis === 'connected' ? 'ok' : 'error'}
                  label={status.redis === 'connected' ? '已连接' : '断开'}
                />
              </div>
              <StatCard title="Tick ID" value={`#${status.world_engine.tick_id}`} icon="⏱️" />
            </div>
          </GlassCard>

          <GlassCard>
            <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2">
              <span>🔧</span> 运维操作
            </h3>
            <div className="flex items-center gap-4 flex-wrap">
              <button
                onClick={() => forceTick.mutate()}
                disabled={forceTick.isPending}
                className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-sakura-400 to-sakura-500 text-white text-sm font-medium hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:scale-105 active:scale-95 shadow-lg shadow-sakura-400/30"
              >
                {forceTick.isPending ? '⏳ 执行中...' : '⚡ 强制 Tick'}
              </button>
              {forceTick.isSuccess && (
                <span className="text-sm text-emerald-600 px-3 py-1.5 rounded-lg bg-emerald-50/80 border border-emerald-200/50">
                  ✅ Tick 已触发
                </span>
              )}
              {forceTick.isError && (
                <span className="text-sm text-red-600 px-3 py-1.5 rounded-lg bg-red-50/80 border border-red-200/50">
                  ❌ 失败: {forceTick.error.message}
                </span>
              )}
            </div>
          </GlassCard>
        </>
      )}
    </div>
  );
}
