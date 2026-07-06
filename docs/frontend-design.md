# 前端设计

> 本文档定义 AI Town Web Dashboard 的页面结构、目录结构、状态管理与实时数据流。前端为 React 19 单页应用，作为运维与观察小镇运行的管理面板。

---

## 一、技术栈

| 类别 | 选型 |
|------|------|
| UI 框架 | React 19 |
| 路由 | TanStack Router |
| 服务端状态 | TanStack Query |
| 客户端状态 | Zustand |
| 构建工具 | Vite 8 |
| 组件库 | shadcn/ui + Tailwind CSS v4 |
| 图表 | Recharts |
| 包管理 | pnpm 11 |
| 类型生成 | openapi-typescript（从后端 OpenAPI 生成） |

---

## 二、页面结构

```text
┌─────────────────────────────────────────────────────────────────────┐
│  Sidebar (导航)   │   Content Area                                 │
│  ─────────────    │                                               │
│  📊 仪表盘        │                                               │
│  🏘️ 小镇管理     │                                               │
│  👤 角色管理      │                                               │
│  🧩 模块管理      │                                               │
│  💬 会话监控      │                                               │
│  📈 可观测性      │                                               │
│  ⚙️ 系统设置      │                                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 三、页面详细功能

| 页面 | 核心功能 | 关键组件 |
|------|----------|----------|
| 仪表盘 | 总览卡片、趋势图、最近事件流 | Recharts、滚动列表 |
| 小镇管理 | 世界状态控制（时间/天气）、场景地图、事件广播 | 可视化地图、控制面板 |
| 角色管理 | 角色列表/创建/编辑、实时状态卡片、关系图谱 | 表格+表单、关系图 |
| 模块管理 | 模块列表（类型/状态/依赖）、开关控制、MCP Server 管理 | 表格、开关组件、日志查看器 |
| 会话监控 | 多渠道会话列表、对话详情、人工干预 | 聊天界面、消息编辑器 |
| 可观测性 | 调用链追踪、日志查询、指标图表、告警配置 | Trace 视图、日志搜索、图表面板 |
| 系统设置 | 模型配置、Prompt 编辑、权限管理 | 表单、代码编辑器 |

---

## 四、目录结构

```text
packages/frontend/
├── index.html
├── package.json
├── pnpm-workspace.yaml
├── vite.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── src/
│   ├── main.tsx                    # 入口文件
│   ├── App.tsx                     # 根组件
│   ├── routes/                     # TanStack Router 路由定义
│   │   ├── __root.tsx
│   │   ├── dashboard/
│   │   ├── town/
│   │   ├── characters/
│   │   ├── modules/
│   │   ├── conversations/
│   │   ├── observability/
│   │   └── settings/
│   ├── components/
│   │   ├── ui/                     # shadcn/ui 基础组件
│   │   ├── layout/                 # 布局组件 (Sidebar, Header)
│   │   ├── dashboard/
│   │   ├── town/
│   │   ├── characters/
│   │   ├── modules/
│   │   └── shared/                 # 通用组件 (Loading, ErrorBoundary)
│   ├── stores/                     # Zustand 状态管理
│   │   ├── app-store.ts
│   │   ├── character-store.ts
│   │   ├── module-store.ts
│   │   └── websocket-store.ts
│   ├── api/                        # OpenAPI 生成的客户端
│   │   ├── client.ts
│   │   ├── types.ts
│   │   └── hooks/                  # TanStack Query hooks
│   ├── hooks/                      # 自定义 Hooks
│   │   ├── use-websocket.ts
│   │   ├── use-toast.ts
│   │   └── use-debounce.ts
│   ├── lib/                        # 工具函数
│   │   ├── utils.ts
│   │   └── constants.ts
│   ├── styles/
│   │   └── globals.css
│   └── types/                      # 共享类型定义
│       └── index.ts
├── public/
├── tests/
│   ├── unit/
│   └── e2e/
└── storybook/
```

---

## 五、实时数据流设计

```text
┌─────────────┐      WebSocket      ┌─────────────────────────────┐
│  后端服务   │ ──────────────────▶ │  前端状态管理               │
│  (FastAPI)  │                     │  ┌─────────────────────────┐│
└─────────────┘                     │  │  Zustand Store (全局)   ││
                                    │  │  - 角色实时状态         ││
                                    │  │  - 世界状态             ││
                                    │  │  - 模块状态             ││
                                    │  └─────────────────────────┘│
                                    │              │              │
                                    │              ▼              │
                                    │  ┌─────────────────────────┐│
                                    │  │  TanStack Query         ││
                                    │  │  (服务端缓存/重试)      ││
                                    │  └─────────────────────────┘│
                                    └─────────────────────────────┘
```

### 5.1 状态分层

| 层 | 工具 | 职责 |
|----|------|------|
| 服务端状态 | TanStack Query | 角色列表、模块列表、消息历史等可缓存数据 |
| 实时状态 | Zustand + WebSocket | 角色实时位置/精力、世界天气、模块健康 |
| UI 状态 | Zustand | 侧边栏折叠、当前选中角色、模态框开关 |

### 5.2 WebSocket Hook

```typescript
// hooks/use-websocket.ts
import { useWebSocketStore } from '@/stores/websocket-store';

export function useDashboardSocket() {
  const ws = useWebSocketStore((s) => s.ws);
  useEffect(() => {
    if (!ws) return;
    const handler = (event: MessageEvent) => {
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case 'character.state_update':
          useCharacterStore.getState().upsert(msg.data);
          break;
        case 'world.state_update':
          useWorldStore.getState().set(msg.data);
          break;
        case 'module.status_change':
          useModuleStore.getState().upsert(msg.data);
          break;
      }
    };
    ws.addEventListener('message', handler);
    return () => ws.removeEventListener('message', handler);
  }, [ws]);
}
```

### 5.3 TanStack Query Hooks

```typescript
// api/hooks/use-characters.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';

export function useCharacters() {
  return useQuery({
    queryKey: ['characters'],
    queryFn: () => api.characters.list(),
  });
}

export function useCreateCharacter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CharacterInput) => api.characters.create(input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['characters'] }),
  });
}
```

---

## 六、API 客户端生成

### 6.1 类型生成

```bash
# 从后端 OpenAPI 生成 TypeScript 类型
pnpm exec openapi-typescript http://localhost:8000/openapi.json \
  -o src/api/types.ts
```

### 6.2 客户端封装

```typescript
// api/client.ts
import type { paths } from './types';

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) throw new ApiError(res.status, await res.json());
  return (await res.json()).data;
}

export const api = {
  characters: {
    list: () => request<paths['/characters']['get']['responses']['200']>('/characters'),
    create: (input: CharacterInput) =>
      request('/characters', { method: 'POST', body: JSON.stringify(input) }),
    // ...
  },
  // ...
};
```

---

## 七、构建与部署

### 7.1 开发

```bash
cd packages/frontend
pnpm install
pnpm dev                  # Vite 开发服务器
pnpm gen:api              # 生成 OpenAPI 类型
```

### 7.2 构建

```bash
pnpm build                # 输出到 dist/
pnpm preview              # 预览生产构建
```

### 7.3 测试

| 类型 | 工具 |
|------|------|
| 单元测试 | Vitest |
| 组件测试 | Testing Library |
| E2E | Playwright |
| 视觉回归 | Storybook + Chromatic（可选） |

---

## 八、关键页面交互

### 8.1 角色管理

- 列表支持搜索（按名字模糊）、过滤（按 status）、排序；
- 详情页含实时状态卡片（WebSocket 推送）、行为时间轴、关系图谱（D3 力导向图）；
- 编辑表单支持 `personality` 标签数组与 `traits` JSONB 动态字段。

### 8.2 模块管理

- 表格展示模块名/类型/状态/依赖；
- 开关组件调用 `POST /modules/{name}/enable|disable`；
- 健康状态徽章颜色：`healthy`(绿) / `unhealthy`(红) / `unknown`(灰)；
- 日志查看器实时滚动展示模块调用日志。

### 8.3 会话监控

- 左侧会话列表（按用户×角色）；
- 右侧聊天界面，支持人工干预（插入 user/assistant 消息）；
- 可切换平台视图（QQ/飞书/Web）。

---

## 九、相关文档

| 主题 | 文档 |
|------|------|
| API 端点 | [api-spec.md](api-spec.md) |
| 可观测性前端展示 | [observability.md](observability.md) |
| 部署 | [deployment.md](deployment.md) |
