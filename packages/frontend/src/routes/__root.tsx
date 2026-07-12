import { createRootRoute, Outlet, redirect } from "@tanstack/react-router";
import { useRouterState } from "@tanstack/react-router";
import { TanStackRouterDevtools } from "@tanstack/router-devtools";
import { NavLayout } from "@/components/ui";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AnimeBackground } from "@/components/AnimeBackground";
import { useAuthStore } from "@/stores/auth";

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
          {import.meta.env.DEV && (
            <TanStackRouterDevtools position="bottom-right" />
          )}
        </div>
      )}
    </ErrorBoundary>
  );
}
