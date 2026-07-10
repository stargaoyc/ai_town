import { Link } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { useAuthStore } from '@/stores/auth';

export function GlassCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`relative bg-glass-bg backdrop-blur-glass-blur rounded-2xl p-6 shadow-soft border border-white/40 hover:shadow-glow transition-shadow ${className}`}>
      {children}
    </div>
  );
}

export function NavLayout({ children }: { children: ReactNode }) {
  const userId = useAuthStore((s) => s.userId);
  const logout = useAuthStore((s) => s.logout);
  const links = [
    { to: '/', label: '总览', icon: '🏠' },
    { to: '/characters', label: '角色', icon: '👥' },
    { to: '/world', label: '世界', icon: '🌍' },
    { to: '/map', label: '地图', icon: '🗺️' },
    { to: '/admin', label: '管理', icon: '⚙️' },
  ];
  return (
    <>
      <nav className="sticky top-0 z-50 bg-glass-bg backdrop-blur-glass-blur border-b border-sakura-200/50 shadow-sm">
        <div className="container mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <span className="text-xl font-bold gradient-text">🌸 AI Town</span>
            <div className="flex gap-1">
              {links.map((link) => (
                <Link
                  key={link.to}
                  to={link.to}
                  className="px-3 py-1.5 rounded-lg text-sm text-twilight-500 hover:bg-sakura-100 hover:text-sakura-600 transition-all hover:scale-105"
                  activeProps={{ className: 'bg-sakura-200/70 text-sakura-700 shadow-sm' }}
                >
                  <span className="mr-1">{link.icon}</span>
                  {link.label}
                </Link>
              ))}
            </div>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-sm text-twilight-400">{userId}</span>
            <button
              onClick={logout}
              className="px-3 py-1.5 rounded-lg text-sm text-twilight-500 hover:bg-red-50 hover:text-red-500 transition-colors"
            >
              退出
            </button>
          </div>
        </div>
      </nav>
      <main className="container mx-auto p-4 relative z-10">{children}</main>
    </>
  );
}

export function StatusBadge({ status, label }: { status: 'ok' | 'error' | 'warning' | 'idle'; label: string }) {
  const colors = {
    ok: 'bg-emerald-100 text-emerald-700 border border-emerald-200/50',
    error: 'bg-red-100 text-red-600 border border-red-200/50',
    warning: 'bg-amber-100 text-amber-700 border border-amber-200/50',
    idle: 'bg-gray-100 text-gray-500 border border-gray-200/50',
  };
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium ${colors[status]}`}>{label}</span>
  );
}

export function StatCard({ title, value, icon, color = 'sakura' }: { title: string; value: string | number; icon?: string; color?: 'sakura' | 'sky' | 'twilight' }) {
  const colorMap = {
    sakura: { text: 'text-sakura-600', bg: 'from-sakura-100 to-sakura-200/50', iconBg: 'bg-sakura-100' },
    sky: { text: 'text-sky-soft-500', bg: 'from-sky-soft-100 to-sky-soft-200/50', iconBg: 'bg-sky-soft-100' },
    twilight: { text: 'text-twilight-500', bg: 'from-twilight-100 to-twilight-200/50', iconBg: 'bg-twilight-100' },
  };
  const c = colorMap[color];
  return (
    <div className={`p-5 rounded-2xl bg-gradient-to-br ${c.bg} border border-white/40 backdrop-blur-sm hover:scale-[1.03] transition-transform`}>
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm text-twilight-400 font-medium">{title}</div>
        {icon && <div className={`w-9 h-9 rounded-xl ${c.iconBg} flex items-center justify-center text-lg`}>{icon}</div>}
      </div>
      <div className={`text-2xl font-bold ${c.text}`}>{value}</div>
    </div>
  );
}

export function LoadingSpinner({ text = '加载中...' }: { text?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <div className="relative">
        <div className="animate-spin rounded-full h-10 w-10 border-4 border-sakura-200" />
        <div className="animate-spin rounded-full h-10 w-10 border-t-4 border-sakura-500 absolute top-0 left-0" />
      </div>
      <span className="text-twilight-400 text-sm">{text}</span>
    </div>
  );
}

export function ErrorDisplay({ error }: { error: Error }) {
  return (
    <div className="p-4 rounded-xl bg-red-50/80 border border-red-200/50 backdrop-blur-sm">
      <div className="flex items-center gap-2 text-red-600 font-medium">
        <span>⚠️</span> 加载失败
      </div>
      <div className="text-sm text-red-500 mt-1 ml-7">{error.message}</div>
    </div>
  );
}

export function ProgressBar({ value, max = 100, color = 'sakura' }: { value: number; max?: number; color?: 'sakura' | 'sky' | 'twilight' }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  const colorMap = {
    sakura: 'from-sakura-300 to-sakura-500',
    sky: 'from-sky-soft-300 to-sky-soft-500',
    twilight: 'from-twilight-300 to-twilight-500',
  };
  return (
    <div className="w-full bg-white/40 rounded-full h-2.5 overflow-hidden shadow-inner">
      <div className={`h-full bg-gradient-to-r ${colorMap[color]} rounded-full transition-all duration-500 shadow-sm`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div className={`animate-pulse bg-white/30 rounded-lg ${className}`} />
  );
}

export function SkeletonCard() {
  return (
    <div className="p-6 rounded-2xl bg-glass-bg backdrop-blur-glass-blur shadow-soft space-y-3">
      <Skeleton className="h-6 w-1/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-2/3" />
      <div className="flex gap-4 mt-4">
        <Skeleton className="h-20 w-20 rounded-xl" />
        <Skeleton className="h-20 w-20 rounded-xl" />
        <Skeleton className="h-20 w-20 rounded-xl" />
      </div>
    </div>
  );
}

export function SkeletonList({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

export function PageHeader({ title, subtitle, icon }: { title: string; subtitle?: string; icon?: string }) {
  return (
    <div className="mb-6 animate-fade-in-up">
      <h1 className="text-2xl font-bold gradient-text flex items-center gap-2">
        {icon && <span>{icon}</span>}
        {title}
      </h1>
      {subtitle && <p className="text-sm text-twilight-400 mt-1">{subtitle}</p>}
    </div>
  );
}

export function EmptyState({ icon = '📭', title, subtitle }: { icon?: string; title: string; subtitle?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <div className="text-5xl mb-3 opacity-60">{icon}</div>
      <div className="text-twilight-400 font-medium">{title}</div>
      {subtitle && <div className="text-sm text-twilight-300 mt-1">{subtitle}</div>}
    </div>
  );
}
