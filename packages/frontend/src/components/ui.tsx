import { Link } from '@tanstack/react-router';
import type { ReactNode } from 'react';

export function GlassCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`bg-glass-bg backdrop-blur-glass-blur rounded-2xl p-6 shadow-soft ${className}`}>
      {children}
    </div>
  );
}

export function NavLayout({ children }: { children: ReactNode }) {
  const links = [
    { to: '/', label: '总览', icon: '🏠' },
    { to: '/characters', label: '角色', icon: '👥' },
    { to: '/world', label: '世界', icon: '🌍' },
    { to: '/map', label: '地图', icon: '🗺️' },
    { to: '/admin', label: '管理', icon: '⚙️' },
  ];
  return (
    <>
      <nav className="sticky top-0 z-50 bg-glass-bg backdrop-blur-glass-blur border-b border-sakura-200/50">
        <div className="container mx-auto px-4 py-3 flex items-center gap-6">
          <span className="text-xl font-bold text-sakura-600">AI Town</span>
          <div className="flex gap-2">
            {links.map((link) => (
              <Link
                key={link.to}
                to={link.to}
                className="px-3 py-1.5 rounded-lg text-sm text-twilight-500 hover:bg-sakura-100 hover:text-sakura-600 transition-colors"
                activeProps={{ className: 'bg-sakura-200 text-sakura-700' }}
              >
                <span className="mr-1">{link.icon}</span>
                {link.label}
              </Link>
            ))}
          </div>
        </div>
      </nav>
      <main className="container mx-auto p-4">{children}</main>
    </>
  );
}

export function StatusBadge({ status, label }: { status: 'ok' | 'error' | 'warning' | 'idle'; label: string }) {
  const colors = {
    ok: 'bg-emerald-100 text-emerald-700',
    error: 'bg-red-100 text-red-700',
    warning: 'bg-amber-100 text-amber-700',
    idle: 'bg-gray-100 text-gray-500',
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[status]}`}>{label}</span>
  );
}

export function StatCard({ title, value, icon, color = 'sakura' }: { title: string; value: string | number; icon?: string; color?: 'sakura' | 'sky' | 'twilight' }) {
  const colorMap = {
    sakura: 'text-sakura-600',
    sky: 'text-sky-soft-500',
    twilight: 'text-twilight-500',
  };
  return (
    <div className="p-4 rounded-xl bg-white/30">
      <div className="text-sm text-twilight-400">{icon && <span className="mr-1">{icon}</span>}{title}</div>
      <div className={`text-2xl font-bold ${colorMap[color]}`}>{value}</div>
    </div>
  );
}

export function LoadingSpinner({ text = '加载中...' }: { text?: string }) {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-sakura-400" />
      <span className="ml-3 text-twilight-400">{text}</span>
    </div>
  );
}

export function ErrorDisplay({ error }: { error: Error }) {
  return (
    <div className="p-4 rounded-xl bg-red-50 border border-red-200">
      <div className="text-red-600 font-medium">加载失败</div>
      <div className="text-sm text-red-500 mt-1">{error.message}</div>
    </div>
  );
}

export function ProgressBar({ value, max = 100, color = 'sakura' }: { value: number; max?: number; color?: 'sakura' | 'sky' | 'twilight' }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  const colorMap = {
    sakura: 'bg-sakura-400',
    sky: 'bg-sky-soft-400',
    twilight: 'bg-twilight-400',
  };
  return (
    <div className="w-full bg-white/40 rounded-full h-2 overflow-hidden">
      <div className={`h-full ${colorMap[color]} rounded-full transition-all`} style={{ width: `${pct}%` }} />
    </div>
  );
}
