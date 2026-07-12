import { defineConfig } from 'vite';
import react, { reactCompilerPreset } from '@vitejs/plugin-react';
import babel from '@rolldown/plugin-babel';
import tailwindcss from '@tailwindcss/vite';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import path from 'node:path';

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
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/metrics': {
        target: 'http://localhost:8000',
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
