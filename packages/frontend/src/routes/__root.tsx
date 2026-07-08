import { createRootRoute, Outlet } from '@tanstack/react-router';
import { TanStackRouterDevtools } from '@tanstack/router-devtools';

export const Route = createRootRoute({
  component: RootComponent,
});

/**
 * 根路由组件
 * - 提供全局布局背景（二次元渐变）
 * - 渲染子路由
 * - 开发环境挂载 TanStack Router Devtools
 */
function RootComponent() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-sakura-100 via-white to-sky-soft-100">
      <Outlet />
      {import.meta.env.DEV && <TanStackRouterDevtools position="bottom-right" />}
    </div>
  );
}
