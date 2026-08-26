import { createRootRoute, Outlet, redirect, Link } from "@tanstack/react-router";
import { useRouterState } from "@tanstack/react-router";
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools";
import { NavLayout, Toaster, GlassCard, EmptyState } from "@/components/ui";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AnimeBackground } from "@/components/AnimeBackground";
import { useAuthStore } from "@/stores/auth";
import { useDashboardSocket } from "@/hooks/useDashboardSocket";

export const Route = createRootRoute({
  component: RootComponent,
  errorComponent: RootErrorComponent,
  notFoundComponent: RootNotFoundComponent,
  beforeLoad: ({ location }) => {
    const { isAuthenticated } = useAuthStore.getState();
    if (!isAuthenticated && location.pathname !== "/login") {
      throw redirect({ to: "/login" });
    }
    if (isAuthenticated && location.pathname === "/login") {
      throw redirect({ to: "/" });
    }
  },
});

// 根路由 errorComponent 会整体替换 RootComponent（含内层 ErrorBoundary），
// 故刻意沿用 ErrorBoundary 的纯元素纪律：不引 framer-motion/ui 组件，
// 只展示 error.message 兜底文案，不暴露堆栈等敏感信息
function RootErrorComponent({ error }: { error: Error }) {
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="bg-white/60 backdrop-blur-xl rounded-3xl p-8 shadow-soft max-w-md w-full text-center border border-white/50">
        <div className="text-5xl mb-4">😵</div>
        <h1 className="text-xl font-bold text-sakura-600 mb-2">页面出错了</h1>
        <p className="text-sm text-twilight-400 mb-6">{error.message || "发生了未知错误"}</p>
        <div className="flex items-center justify-center gap-3">
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-2 rounded-xl bg-sakura-500 text-white text-sm font-medium hover:bg-sakura-600 transition-colors"
          >
            刷新页面
          </button>
          <Link
            to="/"
            className="px-4 py-2 rounded-xl bg-white/70 text-twilight-600 border border-sakura-200/50 text-sm font-medium hover:bg-white/90 transition-colors"
          >
            返回首页
          </Link>
        </div>
      </div>
    </div>
  );
}

function RootNotFoundComponent() {
  return (
    <GlassCard hover={false} className="max-w-lg mx-auto mt-8">
      <EmptyState
        icon="🧭"
        title="页面不存在"
        subtitle="你要找的页面可能已被移动或删除，从首页重新出发吧"
      />
      <div className="flex justify-center pb-4">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 px-5 py-2 rounded-xl bg-gradient-to-r from-sakura-400 to-sakura-500 text-white text-sm font-semibold shadow-md shadow-sakura-400/30 hover:shadow-sakura-400/50 transition-all"
        >
          返回首页
        </Link>
      </div>
    </GlassCard>
  );
}

function RootComponent() {
  // 使用 useRouterState 响应式获取当前路径
  const currentPath = useRouterState({ select: (s) => s.location.pathname });
  const isLoginPage = currentPath === "/login";

  // 登录后订阅仪表盘实时推送（世界状态/通知未读数），降低轮询频率
  useDashboardSocket();

  return (
    <ErrorBoundary>
      <AnimeBackground />
      {isLoginPage ? (
        <Outlet />
      ) : (
        <div className="min-h-screen">
          <NavLayout>
            <Outlet />
          </NavLayout>
          {import.meta.env.DEV && <TanStackRouterDevtools position="bottom-right" />}
        </div>
      )}
      <Toaster />
    </ErrorBoundary>
  );
}
