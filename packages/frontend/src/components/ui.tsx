import { Link } from "@tanstack/react-router";
import type { ReactNode, MouseEventHandler } from "react";
import { motion } from "framer-motion";
import { LogOut, User } from "lucide-react";
import { useAuthStore } from "@/stores/auth";

/* =========================================================
   GlassCard — 强玻璃拟态卡片
   ========================================================= */

export function GlassCard({
  children,
  className = "",
  hover = true,
}: {
  children: ReactNode;
  className?: string;
  hover?: boolean;
}) {
  const content = (
    <>
      <div className="absolute inset-0 bg-gradient-to-br from-white/40 via-transparent to-sakura-100/20 pointer-events-none" />
      <div className="relative z-10">{children}</div>
    </>
  );
  const classString = `relative bg-white/60 backdrop-blur-xl rounded-3xl p-6 border border-white/50 shadow-soft overflow-hidden ${className}`;
  const transitionProps = {
    type: "spring" as const,
    stiffness: 300,
    damping: 24,
  };

  if (!hover) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={transitionProps}
        className={classString}
      >
        {content}
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -4, boxShadow: "0 20px 40px rgba(255, 143, 171, 0.2)" }}
      transition={transitionProps}
      className={classString}
    >
      {content}
    </motion.div>
  );
}

/* =========================================================
   NavLayout — 顶部导航
   ========================================================= */

export function NavLayout({ children }: { children: ReactNode }) {
  const userId = useAuthStore((s) => s.userId);
  const logout = useAuthStore((s) => s.logout);
  const links = [
    { to: "/", label: "总览", icon: "🏠" },
    { to: "/characters", label: "角色", icon: "👥" },
    { to: "/world", label: "世界", icon: "🌍" },
    { to: "/map", label: "地图", icon: "🗺️" },
    { to: "/admin", label: "管理", icon: "⚙️" },
    { to: "/notifications", label: "通知", icon: "🔔" },
    { to: "/settings", label: "设置", icon: "🔧" },
  ];

  const initials = userId ? userId.slice(0, 2).toUpperCase() : "??";

  return (
    <>
      <nav className="sticky top-0 z-50 bg-white/60 backdrop-blur-xl border-b border-white/50 shadow-soft">
        <div className="container mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link
              to="/"
              className="text-xl font-bold gradient-text flex items-center gap-2 hover:scale-105 transition-transform"
            >
              <span className="text-2xl">🌸</span>
              <span>AI Town</span>
            </Link>
            <div className="hidden md:flex gap-1 items-center">
              {links.map((link) => (
                <Link
                  key={link.to}
                  to={link.to}
                  className="px-3 py-1.5 rounded-xl text-sm text-twilight-500 hover:bg-sakura-100/60 hover:text-sakura-600 transition-all hover:scale-105"
                  activeProps={{
                    className: "bg-sakura-200/60 text-sakura-700 shadow-sm",
                  }}
                >
                  <span className="mr-1">{link.icon}</span>
                  {link.label}
                </Link>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 pl-3 pr-1 py-1 rounded-full bg-white/40 border border-white/40">
              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-sakura-400 to-twilight-400 flex items-center justify-center text-white text-xs font-bold">
                <User className="w-4 h-4" />
              </div>
              <span className="text-sm text-twilight-500 font-medium hidden sm:inline">
                {userId}
              </span>
              <span className="text-xs text-twilight-400 px-1.5 py-0.5 rounded-lg bg-white/50 border border-white/30">
                {initials}
              </span>
            </div>
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={logout}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-sm text-twilight-500 hover:bg-red-50 hover:text-red-500 transition-colors"
            >
              <LogOut className="w-4 h-4" />
              <span className="hidden sm:inline">退出</span>
            </motion.button>
          </div>
        </div>
      </nav>
      <main className="container mx-auto p-4 relative z-10">{children}</main>
    </>
  );
}

/* =========================================================
   StatusBadge — 状态徽章
   ========================================================= */

export function StatusBadge({
  status,
  label,
}: {
  status: "ok" | "error" | "warning" | "idle";
  label: string;
}) {
  const colors = {
    ok: "bg-emerald-100/80 text-emerald-700 border border-emerald-200/60 shadow-sm",
    error: "bg-red-100/80 text-red-600 border border-red-200/60 shadow-sm",
    warning:
      "bg-amber-100/80 text-amber-700 border border-amber-200/60 shadow-sm",
    idle: "bg-gray-100/80 text-gray-500 border border-gray-200/60 shadow-sm",
  };
  return (
    <motion.span
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${colors[status]}`}
    >
      {status === "ok" && (
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5 animate-pulse" />
      )}
      {status === "error" && (
        <span className="w-1.5 h-1.5 rounded-full bg-red-500 mr-1.5" />
      )}
      {status === "warning" && (
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500 mr-1.5" />
      )}
      {status === "idle" && (
        <span className="w-1.5 h-1.5 rounded-full bg-gray-400 mr-1.5" />
      )}
      {label}
    </motion.span>
  );
}

/* =========================================================
   StatCard — 统计卡片
   ========================================================= */

export function StatCard({
  title,
  value,
  icon,
  color = "sakura",
}: {
  title: string;
  value: string | number;
  icon?: string;
  color?: "sakura" | "sky" | "twilight";
}) {
  const colorMap = {
    sakura: {
      text: "text-sakura-600",
      bg: "from-sakura-100/80 to-sakura-200/40",
      iconBg: "bg-sakura-100/80",
      glow: "shadow-sakura-400/20",
    },
    sky: {
      text: "text-sky-soft-500",
      bg: "from-sky-soft-100/80 to-sky-soft-200/40",
      iconBg: "bg-sky-soft-100/80",
      glow: "shadow-sky-soft-400/20",
    },
    twilight: {
      text: "text-twilight-500",
      bg: "from-twilight-100/80 to-twilight-200/40",
      iconBg: "bg-twilight-100/80",
      glow: "shadow-twilight-400/20",
    },
  };
  const c = colorMap[color];
  return (
    <motion.div
      whileHover={{ scale: 1.03, y: -2 }}
      transition={{ type: "spring", stiffness: 400, damping: 20 }}
      className={`p-5 rounded-2xl bg-gradient-to-br ${c.bg} border border-white/50 backdrop-blur-sm shadow-soft ${c.glow}`}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm text-twilight-400 font-medium">{title}</div>
        {icon && (
          <div
            className={`w-9 h-9 rounded-xl ${c.iconBg} flex items-center justify-center text-lg shadow-sm`}
          >
            {icon}
          </div>
        )}
      </div>
      <div className={`text-2xl font-bold ${c.text}`}>{value}</div>
    </motion.div>
  );
}

/* =========================================================
   LoadingSpinner — 加载动画
   ========================================================= */

export function LoadingSpinner({ text = "加载中..." }: { text?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <motion.div
        className="relative w-14 h-14"
        animate={{ rotate: 360 }}
        transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
      >
        <div className="absolute inset-0 rounded-full border-4 border-sakura-200/60" />
        <div className="absolute inset-0 rounded-full border-4 border-transparent border-t-sakura-500" />
        <motion.div
          className="absolute inset-2 rounded-full bg-gradient-to-br from-sakura-300/40 to-sakura-400/20 blur-sm"
          animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0.8, 0.5] }}
          transition={{ duration: 2, repeat: Infinity }}
        />
      </motion.div>
      <span className="text-twilight-400 text-sm animate-pulse">{text}</span>
    </div>
  );
}

/* =========================================================
   ErrorDisplay — 错误显示
   ========================================================= */

export function ErrorDisplay({ error }: { error: Error }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="p-5 rounded-2xl bg-red-50/80 border border-red-200/50 backdrop-blur-sm shadow-soft"
    >
      <div className="flex items-center gap-2 text-red-600 font-semibold">
        <span className="text-lg">⚠️</span>
        <span>加载失败</span>
      </div>
      <div className="text-sm text-red-500 mt-1 ml-7">{error.message}</div>
    </motion.div>
  );
}

/* =========================================================
   ProgressBar — 渐变进度条
   ========================================================= */

export function ProgressBar({
  value,
  max = 100,
  color = "sakura",
}: {
  value: number;
  max?: number;
  color?: "sakura" | "sky" | "twilight";
}) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  const colorMap = {
    sakura: "from-sakura-300 to-sakura-500 shadow-sakura-400/30",
    sky: "from-sky-soft-300 to-sky-soft-500 shadow-sky-soft-400/30",
    twilight: "from-twilight-300 to-twilight-500 shadow-twilight-400/30",
  };
  return (
    <div className="w-full bg-white/50 rounded-full h-2.5 overflow-hidden shadow-inner border border-white/30">
      <motion.div
        initial={{ width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: 0.8, ease: "easeOut" }}
        className={`h-full bg-gradient-to-r ${colorMap[color]} rounded-full shadow-md`}
      />
    </div>
  );
}

/* =========================================================
   Skeleton — 骨架屏
   ========================================================= */

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse bg-gradient-to-r from-white/40 via-sakura-100/40 to-white/40 rounded-lg ${className}`}
      style={{ backgroundSize: "200% 100%" }}
    />
  );
}

export function SkeletonCard() {
  return (
    <div className="p-6 rounded-3xl bg-white/50 backdrop-blur-xl border border-white/40 shadow-soft space-y-4">
      <div className="flex items-center gap-3">
        <Skeleton className="h-12 w-12 rounded-2xl" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-5 w-1/3" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      </div>
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

/* =========================================================
   PageHeader — 页面标题
   ========================================================= */

export function PageHeader({
  title,
  subtitle,
  icon,
  backTo,
  backLabel,
}: {
  title: string;
  subtitle?: string;
  icon?: string;
  backTo?: string;
  backLabel?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="mb-6"
    >
      {backTo && (
        <Link
          to={backTo}
          className="inline-flex items-center gap-1.5 text-sm text-twilight-400 hover:text-sakura-600 transition-colors mb-3 group"
        >
          <motion.span whileHover={{ x: -3 }} className="inline-block">
            ←
          </motion.span>
          {backLabel || "返回"}
        </Link>
      )}
      <h1 className="text-2xl md:text-3xl font-bold gradient-text flex items-center gap-3">
        {icon && (
          <motion.span
            animate={{ rotate: [0, 5, -5, 0] }}
            transition={{ duration: 4, repeat: Infinity }}
          >
            {icon}
          </motion.span>
        )}
        {title}
      </h1>
      {subtitle && (
        <p className="text-sm md:text-base text-twilight-400 mt-2 ml-1">
          {subtitle}
        </p>
      )}
    </motion.div>
  );
}

/* =========================================================
   EmptyState — 空状态
   ========================================================= */

export function EmptyState({
  icon = "📭",
  title,
  subtitle,
}: {
  icon?: string;
  title: string;
  subtitle?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="flex flex-col items-center justify-center py-14 text-center"
    >
      <motion.div
        className="text-5xl mb-3 opacity-70"
        animate={{ y: [0, -6, 0] }}
        transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
      >
        {icon}
      </motion.div>
      <div className="text-twilight-500 font-semibold text-lg">{title}</div>
      {subtitle && (
        <div className="text-sm text-twilight-400 mt-1 max-w-xs mx-auto">
          {subtitle}
        </div>
      )}
    </motion.div>
  );
}

/* =========================================================
   AnimeButton — 二次元风格按钮
   ========================================================= */

export function AnimeButton({
  children,
  variant = "primary",
  className = "",
  disabled = false,
  type,
  onClick,
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "danger";
  className?: string;
  disabled?: boolean;
  type?: "button" | "submit" | "reset";
  onClick?: MouseEventHandler<HTMLButtonElement>;
}) {
  const variants = {
    primary:
      "bg-gradient-to-r from-sakura-400 to-sakura-500 text-white shadow-lg shadow-sakura-400/40 hover:shadow-sakura-400/60",
    secondary:
      "bg-white/70 text-twilight-600 border border-sakura-200/50 hover:bg-white/90 hover:border-sakura-300/50 shadow-md",
    danger:
      "bg-gradient-to-r from-red-400 to-red-500 text-white shadow-lg shadow-red-400/40 hover:shadow-red-400/60",
  };
  const classString = `px-5 py-2.5 rounded-xl font-semibold transition-all ${variants[variant]} disabled:opacity-50 disabled:cursor-not-allowed ${className}`;

  if (disabled) {
    return (
      <button type={type} disabled onClick={onClick} className={classString}>
        {children}
      </button>
    );
  }

  return (
    <motion.button
      type={type}
      onClick={onClick}
      className={classString}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
    >
      {children}
    </motion.button>
  );
}

/* =========================================================
   AnimeInput — 二次元风格输入框
   ========================================================= */

export function AnimeInput({
  className = "",
  icon,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { icon?: ReactNode }) {
  return (
    <div className="relative">
      {icon && (
        <div className="absolute left-4 top-1/2 -translate-y-1/2 text-sakura-400">
          {icon}
        </div>
      )}
      <input
        className={`w-full ${icon ? "pl-12" : "pl-4"} pr-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 placeholder:text-twilight-300 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent focus:bg-white/80 transition-all ${className}`}
        {...props}
      />
    </div>
  );
}
