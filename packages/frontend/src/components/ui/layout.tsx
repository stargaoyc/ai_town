import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { LogOut, Menu, User, X } from "lucide-react";
import { useAuthStore } from "@/stores/auth";

/* =========================================================
   NavLayout — 顶部导航
   ========================================================= */

export function NavLayout({ children }: { children: ReactNode }) {
  const userId = useAuthStore((s) => s.userId);
  const logout = useAuthStore((s) => s.logout);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);
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

  useEffect(() => {
    if (!drawerOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    drawerCloseRef.current?.focus();
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [drawerOpen]);

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
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={() => setDrawerOpen(true)}
              aria-label="打开导航菜单"
              aria-expanded={drawerOpen}
              className="md:hidden p-2 rounded-xl text-twilight-500 hover:bg-sakura-100/60 hover:text-sakura-600 transition-colors"
            >
              <Menu className="w-5 h-5" />
            </motion.button>
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

      {/* 移动端抽屉导航（md 以下） */}
      <AnimatePresence>
        {drawerOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[60] bg-black/30 backdrop-blur-sm md:hidden"
            onClick={() => setDrawerOpen(false)}
          >
            <motion.div
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "spring", stiffness: 300, damping: 28 }}
              role="dialog"
              aria-modal="true"
              aria-label="导航菜单"
              onClick={(e) => e.stopPropagation()}
              className="absolute inset-y-0 right-0 w-72 bg-white/80 backdrop-blur-2xl border-l border-white/50 shadow-soft flex flex-col"
            >
              <div className="flex items-center justify-between px-4 py-3 border-b border-white/50">
                <span className="font-bold gradient-text flex items-center gap-2">
                  <span className="text-xl">🌸</span>
                  AI Town
                </span>
                <button
                  ref={drawerCloseRef}
                  onClick={() => setDrawerOpen(false)}
                  aria-label="关闭菜单"
                  className="p-2 rounded-xl text-twilight-400 hover:bg-sakura-100/60 hover:text-sakura-600 transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
              <nav className="flex-1 overflow-y-auto px-3 py-3 space-y-1">
                {links.map((link) => (
                  <Link
                    key={link.to}
                    to={link.to}
                    onClick={() => setDrawerOpen(false)}
                    className="flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm text-twilight-500 hover:bg-sakura-100/60 hover:text-sakura-600 transition-colors"
                    activeProps={{
                      className: "bg-sakura-200/60 text-sakura-700 shadow-sm",
                    }}
                  >
                    <span>{link.icon}</span>
                    {link.label}
                  </Link>
                ))}
              </nav>
              <div className="px-3 pb-4 pt-2 border-t border-white/50">
                <div className="text-xs text-twilight-300 px-3 pb-2">{userId}</div>
                <button
                  onClick={() => {
                    setDrawerOpen(false);
                    logout();
                  }}
                  className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm text-twilight-500 hover:bg-red-50 hover:text-red-500 transition-colors"
                >
                  <LogOut className="w-4 h-4" />
                  退出登录
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <main className="container mx-auto p-4 relative z-10">{children}</main>
    </>
  );
}
