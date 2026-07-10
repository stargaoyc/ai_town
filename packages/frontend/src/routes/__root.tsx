import { createRootRoute, Outlet, redirect } from '@tanstack/react-router';
import { TanStackRouterDevtools } from '@tanstack/router-devtools';
import { NavLayout } from '@/components/ui';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { useAuthStore } from '@/stores/auth';

export const Route = createRootRoute({
  component: RootComponent,
  beforeLoad: () => {
    const { isAuthenticated } = useAuthStore.getState();
    const currentPath = window.location.pathname;
    if (!isAuthenticated && currentPath !== '/login') {
      throw redirect({ to: '/login' });
    }
    if (isAuthenticated && currentPath === '/login') {
      throw redirect({ to: '/' });
    }
  },
});

function RootComponent() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const currentPath = window.location.pathname;
  const isLoginPage = currentPath === '/login';

  if (isLoginPage) {
    return (
      <ErrorBoundary>
        <Outlet />
      </ErrorBoundary>
    );
  }

  if (!isAuthenticated) {
    return (
      <ErrorBoundary>
        <Outlet />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <div className="min-h-screen bg-gradient-to-br from-sakura-100 via-white to-sky-soft-100">
        <NavLayout>
          <Outlet />
        </NavLayout>
        {import.meta.env.DEV && <TanStackRouterDevtools position="bottom-right" />}
      </div>
    </ErrorBoundary>
  );
}
