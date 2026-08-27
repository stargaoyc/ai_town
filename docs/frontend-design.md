# 前端设计

> 本文档定义 AI Town Web Dashboard 的设计语言、页面结构、目录结构、状态管理与实时数据流。前端为 React 19 单页应用，采用**二次元现代风**视觉语言，作为运维与观察小镇运行的管理面板。

---

## 一、技术栈

| 类别       | 技术            | 版本  | 说明                                                        |
| ---------- | --------------- | ----- | ----------------------------------------------------------- |
| UI 渲染    | React           | 19.2  | 并发特性 + Actions                                          |
| 类型       | TypeScript      | 7.0   | 类型系统                                                    |
| 构建       | Vite (Rolldown) | 8.1   | Rust 内核极速构建                                           |
| 编译器     | React Compiler  | 1.0   | 自动记忆化，免手写 useMemo/useCallback                      |
| 路由       | TanStack Router | 1.170 | 类型安全路由，文件路由                                      |
| 服务端状态 | TanStack Query  | 5.101 | 缓存/重试/乐观更新                                          |
| 校验       | Zod             | 4.4   | 表单与 API 运行时校验                                       |
| 客户端状态 | Zustand         | 5.0   | 轻量全局状态                                                |
| 组件库     | shadcn/ui       | 最新  | Radix UI 基础 + 可定制                                      |
| 样式       | Tailwind CSS    | v4    | 原子化 CSS，零运行时                                        |
| Lint       | oxlint          | 最新  | Rust 内核极速 lint（替代 ESLint）                           |
| 格式化     | oxfmt           | 最新  | Rust 内核极速格式化（替代 Prettier）                        |
| 目标语法   | ES2024          | —     | 使用最新 ECMAScript 特性（如 `Set.prototype.intersection`） |
| 图表       | Recharts        | 3.x   | 数据可视化                                                  |
| 动效       | Framer Motion   | 12.x  | 二次元风格过渡动效                                          |
| 图标       | Lucide React    | 最新  | 现代图标库                                                  |
| 包管理     | pnpm            | 11    | 硬链接节省磁盘                                              |

### ES2024 特性示例

启用 ES2024 目标后，可使用最新 ECMAScript 特性：

```typescript
// Set 方法（ES2024）
const activeChars = new Set(["yuina", "koharu"]);
const cafeVisitors = new Set(["yuina", "rin"]);
const intersection = activeChars.intersection(cafeVisitors); // Set {'yuina'}

// Promise.withResolvers（ES2024）
const { promise, resolve, reject } = Promise.withResolvers<string>();
// 无需包装 new Promise((resolve, reject) => { ... })
```

`tsconfig.json` 配置：

```json
{
  "compilerOptions": {
    "target": "ES2024",
    "module": "ESNext",
    "moduleResolution": "bundler"
  }
}
```

### oxlint + oxfmt 配置

oxlint 与 oxfmt 使用同一配置文件（oxlint.json 或 biome.json），共享规则：

```json
// oxlint.json
{
  "categories": {
    "correctness": "error",
    "suspicious": "warn",
    "style": "warn",
    "perf": "warn"
  },
  "rules": {
    "correctness": {
      "noUnusedVariables": "error"
    },
    "style": {
      "noShoutyConstants": "warn"
    }
  }
}
```

运行命令：

```bash
oxlint src/            # lint
oxfmt src/             # 格式化（替代 prettier --write）
oxfmt --check src/     # 检查格式（CI 用）
```

### React Compiler 说明

启用 React Compiler 后，**无需手写 `useMemo`/`useCallback`/`React.memo`**，编译器自动分析并记忆化。代码更简洁，性能更优：

```typescript
// ❌ 旧写法（不再需要）
const value = useMemo(() => compute(a, b), [a, b]);
const handler = useCallback((e) => onClick(e), [onClick]);

// ✅ 新写法（React Compiler 自动优化）
const value = compute(a, b);
const handler = (e) => onClick(e);
```

`vite.config.ts` 启用：

```typescript
import { reactCompiler } from "babel-plugin-react-compiler";

export default defineConfig({
  plugins: [react({ babel: { plugins: [reactCompiler] } })],
});
```

---

## 二、设计语言：二次元现代风

### 2.1 设计关键词

**"轻盈、有呼吸感、带二次元温度"** —— 不堆砌粉嫩卡通元素，而是用现代设计语言承载二次元氛围。

### 2.2 视觉风格组合

| 风格元素                     | 说明                                                              |
| ---------------------------- | ----------------------------------------------------------------- |
| **玻璃拟态 (Glassmorphism)** | 半透明卡片 + 背景模糊 + 细边框，营造轻盈层次感                    |
| **柔和渐变**                 | 樱花粉→天蓝、暮光紫→粉，作为背景与大色块                          |
| **角色卡牌化**               | 角色以"卡牌"形式展示，hover 翻转/浮起，呼应抽卡/立绘文化          |
| **圆角与阴影**               | 大圆角（12–20px）+ 多层柔和阴影，避免尖锐感                       |
| **微动效**                   | Framer Motion 实现入场、悬停、状态切换的弹性动效                  |
| **二次元配色**               | 主色樱粉 `#FF8FAB`、辅色天蓝 `#7EC8E3`、点缀暮紫 `#B19CD9`        |
| **字体**                     | 中文用思源黑体/HarmonyOS Sans，英文用 Inter，标题可选衬线增加质感 |

### 2.3 配色系统（Tailwind v4 CSS 变量）

```css
/* styles/globals.css */
@import "tailwindcss";

@theme {
  /* 主色调 - 樱花粉 */
  --color-sakura-50: #fff5f8;
  --color-sakura-500: #ff8fab;
  --color-sakura-600: #f472a3;

  /* 辅色 - 天蓝 */
  --color-sky-soft-400: #7ec8e3;
  --color-sky-soft-500: #5bb5d8;

  /* 点缀 - 暮紫 */
  --color-twilight-400: #b19cd9;
  --color-twilight-500: #9b7ed6;

  /* 玻璃拟态 */
  --color-glass-bg: rgba(255, 255, 255, 0.55);
  --color-glass-border: rgba(255, 255, 255, 0.35);
  --color-glass-blur: 16px;

  /* 圆角 */
  --radius-card: 16px;
  --radius-pill: 9999px;

  /* 阴影 */
  --shadow-soft: 0 4px 20px rgba(255, 143, 171, 0.12);
  --shadow-hover: 0 8px 32px rgba(255, 143, 171, 0.2);
}
```

### 2.4 玻璃拟态卡片组件示例

```tsx
// components/ui/glass-card.tsx
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export function GlassCard({ children, className, hover = true }: GlassCardProps) {
  return (
    <motion.div
      whileHover={hover ? { y: -4, scale: 1.01 } : undefined}
      transition={{ type: "spring", stiffness: 300, damping: 24 }}
      className={cn(
        "rounded-[--radius-card] border border-[--color-glass-border]",
        "bg-[--color-glass-bg] backdrop-blur-[--color-glass-blur]",
        "shadow-[--shadow-soft] hover:shadow-[--shadow-hover]",
        "transition-shadow",
        className,
      )}
    >
      {children}
    </motion.div>
  );
}
```

### 2.5 角色卡牌组件（二次元立绘风）

```tsx
// components/characters/character-card.tsx
import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/glass-card";

export function CharacterCard({ character }: { character: Character }) {
  return (
    <GlassCard className="overflow-hidden group">
      {/* 立绘区 - 渐变背景 + 头像 */}
      <div className="relative h-64 bg-gradient-to-br from-sakura-50 via-sky-soft-400/30 to-twilight-400/30">
        <img
          src={character.avatar_url}
          alt={character.name}
          className="absolute inset-0 w-full h-full object-cover object-top
                     transition-transform duration-500 group-hover:scale-105"
        />
        {/* 状态徽章 */}
        <span
          className="absolute top-3 right-3 px-3 py-1 rounded-full
                         bg-white/80 backdrop-blur text-xs font-medium"
        >
          {character.mood}
        </span>
      </div>

      {/* 信息区 */}
      <div className="p-5">
        <h3 className="text-lg font-semibold">{character.name}</h3>
        <p className="text-sm text-muted-foreground">{character.occupation}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {character.personality.map((tag) => (
            <span
              key={tag}
              className="px-2 py-0.5 text-xs rounded-full
                         bg-sakura-500/15 text-sakura-600"
            >
              {tag}
            </span>
          ))}
        </div>

        {/* 状态条 - 精力/饥饿 */}
        <div className="mt-4 space-y-2">
          <StatBar label="精力" value={character.energy} color="bg-sky-soft-500" />
          <StatBar label="饱腹" value={100 - character.hunger} color="bg-sakura-500" />
        </div>
      </div>
    </GlassCard>
  );
}
```

### 2.6 页面布局

```text
┌─────────────────────────────────────────────────────────────────────┐
│  顶部栏: Logo + 世界时钟 + 天气 + 全局搜索                          │
├──────────┬──────────────────────────────────────────────────────────┤
│          │                                                          │
│  侧边栏  │   主内容区 (玻璃拟态卡片网格)                            │
│  (毛玻璃)│                                                          │
│          │   ┌────────┐ ┌────────┐ ┌────────┐                      │
│  📊 仪表盘│   │ 角色卡 │ │ 角色卡 │ │ 角色卡 │                      │
│  🏘️ 小镇 │   └────────┘ └────────┘ └────────┘                      │
│  👥 角色 │                                                          │
│  🧩 模块 │   ┌────────────────────────────────────────┐            │
│  💬 会话 │   │  实时事件流 (左侧滚动列表)              │            │
│  📈 观测 │   └────────────────────────────────────────┘            │
│  ⚙️ 设置 │                                                          │
│          │                                                          │
└──────────┴──────────────────────────────────────────────────────────┘
```

侧边栏采用毛玻璃效果，悬浮于渐变背景之上。背景使用动态渐变（随虚拟时间变化：清晨粉橙→正午天蓝→黄昏紫粉→深夜靛蓝）。

---

## 三、页面详细功能

实际已实现 24 个页面，覆盖完整的管理与监控场景：

| 页面路由                                      | 核心功能                                    | 关键组件                      |
| --------------------------------------------- | ------------------------------------------- | ----------------------------- |
| `/` 仪表盘                                    | 总览卡片、趋势图、最近事件流、世界时钟      | GlassCard、Recharts、滚动列表 |
| `/characters` 角色列表                        | 角色卡牌墙、状态/位置/当前行为              | CharacterCard、状态条         |
| `/characters/$characterId` 角色详情           | 状态、记忆、计划、关系、行为日志            | 标签页切换、Recharts          |
| `/characters/import` 角色导入                 | YAML/JSON 角色卡导入                        | 表单、文件上传                |
| `/characters/compare` 角色对比                | 多角色状态对比                              | 对比表格、Recharts            |
| `/map` 小镇地图                               | 场景热力图、拥挤度可视化                    | 可视化地图                    |
| `/memory` 记忆时间线                          | 记忆查询、重要程度筛选                      | 时间线、向量检索              |
| `/memory/$characterId/reflections` 反思查看器 | 反思列表、来源记忆追溯                      | 卡片列表                      |
| `/memory/vector-search` 向量检索测试          | 输入查询、查看相似记忆                      | 搜索表单、结果列表            |
| `/plans` 规划系统                             | 角色计划列表、进度跟踪                      | 看板、进度条                  |
| `/relations` 关系图谱                         | 角色关系网络可视化                          | 关系图、力导向图              |
| `/events` 世界事件时间线                      | 事件流、可展开 EventCard                    | Framer Motion AnimatePresence |
| `/actions` 角色行为日志                       | 行为历史、参数详情                          | 表格、JSON 查看器             |
| `/messages` QQ 消息监控                       | 消息列表、会话详情                          | 聊天气泡、消息编辑器          |
| `/messages/proactive` 主动分享历史            | 分享记录、文案查看                          | 卡片列表                      |
| `/messages/export` 聊天记录导出               | 导出会话记录                                | 表单、下载                    |
| `/conversations` 会话管理                     | 多渠道会话列表                              | 表格                          |
| `/monitoring` 监控                            | Grafana 集成 + 原生日志/指标面板            | iframe、GlassCard             |
| `/monitoring/llm-cost` LLM 成本仪表盘         | Token 消耗、成本趋势                        | Recharts                      |
| `/snapshots` 世界快照管理                     | 快照列表、冷启动恢复                        | 表格、操作按钮                |
| `/character-cards` 角色卡预览                 | 角色卡渲染预览                              | 卡片组件                      |
| `/notifications` 通知中心                     | 系统通知列表                                | 列表                          |
| `/settings` 系统设置                          | 模型配置、Prompt 编辑、**工具命名空间开关** | 表单、toggle 控件             |
| `/settings/tools` 工具命名空间管理            | 工具命名空间列表、工具清单、启用状态        | 表格、toggle                  |

### 3.1 工具命名空间开关 UI

设置页 `/settings` 中的工具命名空间卡片提供 toggle 开关（原 MCP Server 卡片，2026-07-15 转换为本地工具）：

- **启用状态**：sakura 色主题（樱花粉 #FF8FAB），显示"已启用"绿色标签；
- **禁用状态**：灰色 + `opacity-70`，显示"已禁用"灰色标签；
- **交互**：点击 toggle 立即调用 `PUT /api/v1/tools/servers/{name}/enabled`，成功后 TanStack Query 自动刷新列表；
- **数据流**：`useToggleMcpServer` mutation → `toggleMcpServer` API → 后端 Redis `hset tools:enabled`。

详见 [模块与本地工具系统设计 - 工具命名空间单独开关](module-system.md#51-工具命名空间单独开关redis-持久化)。

---

## 四、目录结构

> 实际结构（round-7 审查对账：与早期规划差异——路由为扁平 TSX 文件、组件层仅保留少量全局组件、无 storybook/e2e 目录；OpenAPI 客户端为 `lib/api.ts` + `lib/api-types.ts` + `types/api-generated.d.ts`，未落 `api/` 目录）。

```text
packages/frontend/
├── index.html
├── package.json
├── pnpm-workspace.yaml
├── vite.config.ts              # 含 React Compiler 配置
├── oxlint.config.json          # oxlint 配置
├── tailwind.config.ts
├── tsconfig.json
├── src/
│   ├── main.tsx                # 入口（QueryClient + RouterProvider）
│   ├── routeTree.gen.ts        # TanStack Router 生成路由树
│   ├── routes/                 # TanStack Router 文件路由（扁平 TSX）
│   │   ├── __root.tsx          # 根布局（登录守卫 + Dashboard Socket 挂载）
│   │   ├── login.tsx / index.tsx（仪表盘）
│   │   ├── characters.tsx / characters.$characterId.tsx / import.tsx / compare.tsx
│   │   ├── map.tsx / world.tsx / events.tsx / actions.tsx / state-charts.tsx
│   │   ├── memories.tsx / vector-search.tsx / reflections.tsx / diaries.tsx / person-memory.tsx
│   │   ├── plans.tsx / relationships.tsx / snapshots.tsx / notifications.tsx
│   │   ├── conversations.tsx / qq-monitor.tsx / shares.tsx / export.tsx
│   │   ├── monitoring.tsx / metrics.tsx / cost.tsx / admin.tsx / settings.tsx
│   │   └── character-card.tsx（角色卡预览）
│   ├── components/
│   │   ├── ui/                 # 基础组件（feedback / layout / primitives / index）
│   │   ├── AnimeBackground.tsx # 动态渐变背景
│   │   └── ErrorBoundary.tsx
│   ├── stores/                 # Zustand（客户端状态）
│   │   ├── auth.ts
│   │   └── toast.ts
│   ├── hooks/
│   │   ├── useChatSocket.ts    # 角色对话 WebSocket
│   │   └── useDashboardSocket.ts  # Dashboard 实时订阅
│   ├── lib/
│   │   ├── api.ts              # API 客户端（request + 方法）
│   │   ├── api-types.ts        # API 契约类型（引用 api-generated）
│   │   ├── queries.ts          # TanStack Query hooks
│   │   └── format.ts
│   ├── types/
│   │   └── api-generated.d.ts  # openapi-typescript 生成
│   └── index.css               # Tailwind v4 + 主题变量
└── public/
```

---

## 五、实时数据流设计

```text
┌─────────────┐      WebSocket      ┌─────────────────────────────┐
│  后端服务   │ ──────────────────▶ │  前端状态管理               │
│  (FastAPI)  │                     │  ┌─────────────────────────┐│
└─────────────┘                     │  │  Zustand Store (实时)   ││
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

| 层         | 工具                       | 职责                                     |
| ---------- | -------------------------- | ---------------------------------------- |
| 服务端状态 | TanStack Query             | 角色列表、消息历史等可缓存数据；轮询仅作断连兜底 |
| 实时推送   | `useDashboardSocket` + Query 失效 | `/ws/dashboard` 帧直接写入/失效 Query 缓存 |
| UI 状态    | Zustand（auth-store 等）   | 登录态、当前选中角色、模态框开关         |

### 5.2 Dashboard 实时订阅（已实现：`src/hooks/useDashboardSocket.ts`）

登录后于根布局挂载，订阅 `/ws/dashboard?token=JWT`：

- 收到 `dashboard` 帧 → 世界字段直接写入 `["world"]` Query 缓存，`invalidateQueries(["health"])`
- 通知未读数变化 → `invalidateQueries(["notifications"])`
- 断线指数退避重连（上限 10 次）；未登录不建立连接

### 5.3 TanStack Query Hooks（Zod 校验）

```typescript
// api/hooks/use-characters.ts
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "../client";

const CharacterSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  age: z.number(),
  occupation: z.string(),
  personality: z.array(z.string()),
  status: z.enum(["active", "archived", "deleted"]),
});

export function useCharacters() {
  return useQuery({
    queryKey: ["characters"],
    queryFn: async () => {
      const raw = await api.characters.list();
      return z.array(CharacterSchema).parse(raw); // 运行时校验
    },
  });
}
```

---

## 六、API 客户端生成

```bash
# 从后端 OpenAPI 生成 TypeScript 类型
pnpm exec openapi-typescript http://localhost:8000/openapi.json \
  -o src/api/types.ts
```

---

## 七、动态背景

背景渐变随虚拟世界时间变化，增强沉浸感：

```tsx
// components/layout/dynamic-background.tsx
const GRADIENTS = {
  dawn: "from-[#FFE5B4] via-[#FFCCE5] to-[#B19CD9]", // 清晨粉橙
  day: "from-[#A8D8FF] via-[#C7CEEA] to-[#E0C3FC]", // 正午天蓝
  dusk: "from-[#FFB088] via-[#FF8FAB] to-[#B19CD9]", // 黄昏紫粉
  night: "from-[#2C3E50] via-[#4A4063] to-[#6C5B7B]", // 深夜靛蓝
};

export function DynamicBackground({ worldTime }: { worldTime: Date }) {
  const phase = getDayPhase(worldTime); // dawn | day | dusk | night
  return (
    <div
      className={`fixed inset-0 -z-10 bg-gradient-to-br ${GRADIENTS[phase]}
                     transition-all duration-[3000ms]`}
    />
  );
}
```

---

## 八、构建与部署

### 8.1 开发

```bash
cd packages/frontend
pnpm install
pnpm gen:api                # 生成 OpenAPI 类型
pnpm dev                    # Vite 开发服务器
```

### 8.2 Lint 与格式化

```bash
pnpm lint                   # oxlint + oxfmt --check
pnpm format                 # oxfmt src/
pnpm typecheck              # tsc --noEmit
```

`oxlint.json` 示例（oxlint + oxfmt 共用）：

```json
{
  "$schema": "https://raw.githubusercontent.com/oxlint/oxlint/main/schema.json",
  "categories": {
    "correctness": "error",
    "suspicious": "warn",
    "style": "warn",
    "perf": "warn"
  },
  "rules": {
    "correctness": {
      "noUnusedVariables": "error"
    },
    "react": {
      "exhaustiveDeps": "off"
    },
    "reactHooks": {
      "rulesOfHooks": "error"
    }
  },
  "ignorePatterns": ["dist", "node_modules", "src/api/types.ts"]
}
```

> React Compiler 启用后，`exhaustiveDeps` 可关闭——编译器已保证依赖正确性。oxfmt 会自动遵循 oxlint.json 的配置。

### 8.3 构建

```bash
pnpm build                  # 输出到 dist/
pnpm preview                # 预览生产构建
```

### 8.4 测试

| 类型     | 工具                          |
| -------- | ----------------------------- |
| 单元测试 | Vitest                        |
| 组件测试 | Testing Library               |
| E2E      | Playwright                    |
| 视觉回归 | Storybook + Chromatic（可选） |

---

## 九、相关文档

| 主题             | 文档                                         |
| ---------------- | -------------------------------------------- |
| API 端点         | [api-spec.md](api-spec.md)                   |
| 可观测性前端展示 | [observability.md](observability.md)         |
| 部署             | [deployment.md](deployment.md)               |
| Docker 部署      | [docker-deployment.md](docker-deployment.md) |
