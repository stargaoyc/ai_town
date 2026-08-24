# packages/frontend/Dockerfile
# AI Town 前端镜像 - Node 22 + pnpm + Vite 构建 + Nginx 静态服务
# 多阶段构建：builder 阶段编译前端，运行阶段用 Nginx 提供静态文件

# ===== Builder 阶段 =====
FROM node:22-alpine AS builder

RUN npm install -g pnpm@11

WORKDIR /app

# 先复制依赖文件（利用 Docker 层缓存）
COPY package.json pnpm-lock.yaml ./

RUN pnpm install --frozen-lockfile

# 复制源码并构建
COPY . .
RUN pnpm build

# ===== 运行阶段 =====
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置（支持 SPA 路由 + API 反代）
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
