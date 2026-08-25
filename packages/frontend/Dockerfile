# packages/frontend/Dockerfile
# AI Town 前端镜像 - Node 22 + pnpm + Vite 构建 + Nginx 静态服务
# 多阶段构建：builder 阶段编译前端，运行阶段用 Nginx 提供静态文件

# ===== Builder 阶段 =====
# 构建上下文为仓库根（pnpm 工作区锁文件在根目录，见 compose build.context）
FROM node:22-alpine AS builder

RUN npm install -g pnpm@11

WORKDIR /app

# 先复制工作区清单与依赖文件（利用 Docker 层缓存）；
# 锁文件 importer 仅含 packages/frontend，无需根 package.json
COPY pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/frontend/package.json ./packages/frontend/

RUN pnpm install --frozen-lockfile

# 复制前端源码并构建（filter 定位到前端包，避免触发其他工作区成员）
COPY packages/frontend ./packages/frontend
RUN pnpm --filter ai-town-frontend build

# ===== 运行阶段 =====
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /app/packages/frontend/dist /usr/share/nginx/html

# 复制 Nginx 配置（主配置重定向 pid/临时目录到非 root 可写路径；server 配置支持 SPA + 反代）
COPY packages/frontend/nginx.main.conf /etc/nginx/nginx.conf
COPY packages/frontend/nginx.conf /etc/nginx/conf.d/default.conf

# 非 root 用户运行（nginx uid 101）：静态目录授予读权限
RUN chown -R nginx:nginx /usr/share/nginx/html

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
