import { defineConfig, loadEnv } from 'vite';
import react, { reactCompilerPreset } from '@vitejs/plugin-react';
import babel from '@rolldown/plugin-babel';
import tailwindcss from '@tailwindcss/vite';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import path from 'node:path';

// 后端端口解析顺序：进程环境变量 > .env.local（本机固化）> 默认 8000
const envFiles = loadEnv(process.env.NODE_ENV ?? 'development', process.cwd(), '');
const backendPort = process.env.BACKEND_PORT || envFiles.BACKEND_PORT || '8000';
const backendOrigin = `http://localhost:${backendPort}`;
const backendWsOrigin = backendOrigin.replace('http', 'ws');

export default defineConfig({
  plugins: [
    // TanStack Router 自动路由和代码分割（必须在 react() 之前）
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
    }),
    // React 插件（v6 移除了内置 Babel，改用 oxc）
    react(),
    // React Compiler 1.0 — 通过 @rolldown/plugin-babel 运行 Babel 插件
    babel({
      include: /\.[jt]sx?$/,
      presets: [reactCompilerPreset({ target: '19' })],
    }),
    // Tailwind CSS Vite 插件（V4 版本）
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    target: 'es2024', // 现代浏览器支持
  },
  test: {
    environment: 'node', // 冒烟测试聚焦纯逻辑（queryKeys/store/api 错误处理），DOM 组件测试待引入 jsdom
    include: ['src/**/*.test.{ts,tsx}'],
  },
  server: {
    proxy: {
      '/api': {
        target: backendOrigin,
        changeOrigin: true,
      },
      '/ws': {
        target: backendWsOrigin,
        ws: true,
      },
      '/health': {
        target: backendOrigin,
        changeOrigin: true,
      },
      '/metrics': {
        target: backendOrigin,
        changeOrigin: true,
        bypass: (req) => {
          // Don't proxy HTML page navigation, only proxy API/fetch requests
          if (req.headers.accept?.includes('text/html')) {
            return '/index.html';
          }
        },
      },
    },
  },
});
