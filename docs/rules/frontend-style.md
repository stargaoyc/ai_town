# 前端编码规范（TypeScript / React）

> 适用范围：`packages/frontend/src` 下所有 TypeScript / TSX 代码。
>
> 后端规范见 [implementation-style.md](implementation-style.md)；本文件与其共享同一套核心原则，
> 仅约定前端特定的落地方式。

---

## 一、六大核心原则（前端映射）

后端 [implementation-style.md §一](implementation-style.md#一六大核心原则) 的六条原则在前端的落地：

| 原则 | 前端落地 |
|------|----------|
| 主流程优先 | 路由组件的数据加载 → 渲染 → 交互按序平铺，不把逻辑拆散进多层 HOC/自定义 hook |
| 少加概念 | 不为单个页面建抽象组件；shadcn/ui 已有的组件直接用 |
| 单一真相源 | 服务端数据只存 TanStack Query 缓存，不复制进 Zustand；认证态只存 auth store |
| 显式边界 | 组件 props 用 interface 显式声明；API 响应类型与后端字段一一对应 |
| 少量重复优于错误抽象 | 两个页面的相似表单不强行抽成「通用表单引擎」 |
| 注释解释约束 | 只解释「为什么」（如变通方案、浏览器兼容），不解释「是什么」 |

---

## 二、技术栈与工具链

| 层次 | 选型 | 说明 |
|------|------|------|
| 框架 | React 19 + React Compiler | **不要手写 `useMemo` / `useCallback` / `React.memo`**，Compiler 自动记忆化 |
| 路由 | TanStack Router（文件路由） | `src/routes/` 目录结构即路由，`routeTree.gen.ts` 为生成物勿手改 |
| 数据获取 | TanStack Query | 所有服务端数据经 `lib/queries.ts` 的 hook 获取 |
| 客户端状态 | Zustand | 仅存放跨页面客户端状态（当前只有 auth store） |
| 运行时校验 | Zod | 对关键 API 响应做解析校验（逐步落地） |
| 样式 | Tailwind CSS v4 + shadcn/ui | 优先组合既有 UI 组件，不自造基础控件 |
| Lint / 格式化 | oxlint + oxfmt | `pnpm run lint` / `pnpm run format`，不用 ESLint/Prettier |
| 类型检查 | `tsc --noEmit` | `pnpm run typecheck`，严格模式 |

---

## 三、目录结构与职责

```text
packages/frontend/src/
├── routes/        # TanStack Router 文件路由（页面级组件 + 数据加载）
├── components/    # 可复用展示组件（Glassmorphism 风格）
├── lib/
│   ├── api.ts     # 唯一 API 客户端：所有 HTTP 请求必须经过它
│   └── queries.ts # queryKeys 工厂 + useXxxQuery/useXxxMutation hooks
├── stores/        # Zustand store（仅客户端状态）
└── hooks/         # 跨页面复用的自定义 hook
```

职责红线：

| 目录 | 允许 | 禁止 |
|------|------|------|
| `routes/` | 页面组装、Query 调用、交互处理 | 业务规则计算、直接 `fetch`、内联 mock 数据 |
| `components/` | 纯展示 + 回调 props | 发起请求、读写 store 以外的全局副作用 |
| `lib/api.ts` | 请求封装、鉴权头注入、401 处理 | 业务判断 |
| `stores/` | 客户端状态 | 缓存服务端数据（那是 Query 的职责） |

---

## 四、组件编写规范

### 4.1 函数组件 + 显式 props 类型

```tsx
// ✅
interface CharacterCardProps {
  character: Character;
  onSelect?: (id: string) => void;
}

export function CharacterCard({ character, onSelect }: CharacterCardProps) {
  return (
    <button onClick={() => onSelect?.(character.id)}>{character.name}</button>
  );
}
```

```tsx
// ❌ props 用 any / 内联匿名对象解构无类型
export function CharacterCard({ character, onSelect }: any) { ... }
```

### 4.2 React Compiler 约定

- **禁止手写 `useMemo` / `useCallback` / `React.memo`**——Compiler 自动完成；
  手写反而干扰其依赖分析。
- 遵守 React Rules：渲染期保持纯函数，副作用全部放进事件处理器或 `useEffect`。

### 4.3 组件拆分标准

- 一个路由文件超过 ~300 行时，把局部 UI 块拆到同目录或 `components/`。
- 拆分依据是「渲染块」而非「逻辑块」；逻辑复用走自定义 hook，不走继承。

---

## 五、数据获取与状态管理

### 5.1 所有请求必须经过 `lib/api.ts`

```tsx
// ✅ 经统一客户端（自动携带鉴权头、统一 401 处理）
import { api } from "@/lib/api";
const data = await api.getConversations();

// ❌ 裸 fetch：丢鉴权头、无 401 处理、错误处理各自为政
const res = await fetch("/api/v1/metrics/");
```

新增端点时在 `api.ts` 补对应方法与响应类型，不在组件里拼 URL。

### 5.2 TanStack Query 约定

- queryKey 一律使用 `lib/queries.ts` 的 **queryKeys 工厂**，禁止内联裸字符串 key：

```ts
// ✅
useQuery({ queryKey: queryKeys.character(id), queryFn: ... });

// ❌
useQuery({ queryKey: ["character", id], queryFn: ... });
```

- 轮询间隔（`refetchInterval`）集中在 queries.ts 定义并注明原因；新增轮询前先确认
  该数据是否适合改为 WebSocket 推送（见 docs/design-improvement-and-fixes.md F-1）。
- 写操作用 `useMutation` 并在 onSuccess 中 `invalidateQueries`，不手动 setQueryData
  拼凑缓存。

### 5.3 Zustand 边界

- 只放**客户端状态**（登录态、UI 偏好）；服务端数据的真相源是 Query 缓存。
- store 按 domain 拆分（如 `auth.ts`），不建单一巨型 store。

### 5.4 禁止前端伪造业务数据

- 禁止硬编码用户标识（如 `userId: "web_user"`）——用户身份来自 auth store。
- 禁止在路由组件内置 mock 数据生成器写入后端（历史教训：notifications 页面的
  `Math.random()` 模拟通知）。演示数据一律走后端 seed 或独立开发开关。

---

## 六、TypeScript 类型规范

### 6.1 基本约定

| 场景 | 规范 |
|------|------|
| 对象形状 | `interface`；联合/工具类型用 `type` |
| 与后端契约 | 在 `lib/api.ts` 中显式声明响应类型，字段名与后端 JSON 一致（snake_case 保持原样） |
| 可空 | `T \| null` / `T \| undefined`，禁用隐式 |
| 枚举 | 优先字面量联合类型 `"web" \| "qq" \| "lark"`，少用 enum |

### 6.2 禁止项

- **禁止 `any`**：确实动态的场景用 `unknown` + 收窄，或 Zod 解析产出类型。
- **禁止 `@ts-ignore`**；`@ts-expect-error` 仅允许第三方库类型缺陷且须注释原因与上游 issue。
- 禁止 `as` 双重断言绕过检查；DOM 事件等合法收窄场景除外。

### 6.3 类型同步机制

后端 FastAPI 自动产出 OpenAPI；API 类型变更时同步更新 `lib/api.ts`
（openapi-typescript 生成为规划项，落地前手工维护但必须在 PR 中核对字段）。

---

## 七、样式规范

- 布局与视觉一律 Tailwind 工具类；重复出现 ≥3 次的组合才提取为组件，不建自定义 CSS 类。
- 优先使用 `components/ui/` 下 shadcn/ui 组件；需要主题化的新样式遵循现有
  Glassmorphism 风格变量。
- 禁止内联 `style={{}}` 做能用工具类表达的事；动态数值（进度条宽度等）除外。

---

## 八、常见坏代码形态（前端版）

### 8.1 状态复制型

```tsx
// ❌ 把 Query 数据复制进 useState，产生两份真相
const { data } = useCharacters();
const [chars, setChars] = useState(data ?? []);

// ✅ 直接消费 Query 数据；写操作走 mutation + invalidate
const { data: chars } = useCharacters();
```

### 8.2 散弹式请求型

```tsx
// ❌ 组件里各自 fetch、各自管理 loading/error
useEffect(() => {
  fetch(`/api/v1/characters/${id}`).then(...)
}, [id]);

// ✅ 统一走 queries.ts 的 hook
const { data, isPending } = useCharacter(id);
```

### 8.3 隐式契约型

```tsx
// ❌ 依赖后端未文档化的字段，类型上撒谎
const user = res as { user_id: string };

// ✅ 响应类型显式声明在 api.ts，必要时 Zod 解析
```

### 8.4 条件渲染嵌套地狱型

```tsx
// ❌ 五层三元表达式
// ✅ 提前 return + 小组件拆分；加载/错误/空态三分支平铺
if (isPending) return <Skeleton />;
if (error) return <ErrorState />;
if (!data?.length) return <EmptyState />;
return <List items={data} />;
```

---

## 九、自查清单

提交前逐项确认：

- [ ] `pnpm run lint` 与 `pnpm run typecheck` 全部通过
- [ ] 无新增 `any` / `@ts-ignore` / 裸 `fetch`
- [ ] 新端点已在 `lib/api.ts` 补方法与类型；queryKey 走工厂
- [ ] 未硬编码用户标识 / mock 数据；服务端数据未被复制进本地 state
- [ ] 未手写 `useMemo` / `useCallback` / `React.memo`
- [ ] 组件 props 有显式 interface；事件回调命名 `onXxx`

---

## 相关文档

- [implementation-style.md](implementation-style.md) —— 后端 Python 规范与共同原则原文
- [domain-design-style.md](domain-design-style.md) —— 分层与领域组织
- [../development-guide.md](../development-guide.md) —— 本地开发流程
