import { createRootRoute, Outlet, redirect } from "@tanstack/react-router";
import { useRouterState } from "@tanstack/react-router";
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools";
import { NavLayout, Toaster } from "@/components/ui";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AnimeBackground } from "@/components/AnimeBackground";
import { useAuthStore } from "@/stores/auth";
import { useDashboardSocket } from "@/hooks/useDashboardSocket";

export const Route = createRootRoute({
  component: RootComponent,
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
