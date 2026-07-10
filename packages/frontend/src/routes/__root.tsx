import { createRootRoute, Outlet, redirect } from "@tanstack/react-router";
import { TanStackRouterDevtools } from "@tanstack/router-devtools";
import { NavLayout } from "@/components/ui";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AnimeBackground } from "@/components/AnimeBackground";
import { useAuthStore } from "@/stores/auth";

export const Route = createRootRoute({
  component: RootComponent,
  beforeLoad: () => {
    const { isAuthenticated } = useAuthStore.getState();
    const currentPath = window.location.pathname;
    if (!isAuthenticated && currentPath !== "/login") {
      throw redirect({ to: "/login" });
    }
    if (isAuthenticated && currentPath === "/login") {
      throw redirect({ to: "/" });
    }
  },
});

function RootComponent() {
  const currentPath = window.location.pathname;
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
