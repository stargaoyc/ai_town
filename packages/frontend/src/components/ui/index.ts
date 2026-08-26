// P1-16：原单文件 ui.tsx 拆分为 layout / primitives / feedback 三模块，
// barrel 保持既有 `from "@/components/ui"` 导入路径不变。
export {
  GlassCard,
  StatusBadge,
  StatCard,
  ProgressBar,
  Skeleton,
  SkeletonCard,
  SkeletonList,
  PageHeader,
  AnimeButton,
  AnimeInput,
} from "./primitives";
export { NavLayout } from "./layout";
export { LoadingSpinner, ErrorDisplay, EmptyState, Toaster, ConfirmDialog } from "./feedback";
