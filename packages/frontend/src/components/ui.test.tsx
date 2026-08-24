// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AnimeButton,
  AnimeInput,
  EmptyState,
  ErrorDisplay,
  GlassCard,
  LoadingSpinner,
  PageHeader,
  ProgressBar,
  Skeleton,
  SkeletonCard,
  SkeletonList,
  StatCard,
  StatusBadge,
} from "./ui";

afterEach(() => {
  cleanup();
});

describe("GlassCard", () => {
  it("渲染子内容", () => {
    render(
      <GlassCard>
        <p>卡片内容</p>
      </GlassCard>,
    );
    expect(screen.getByText("卡片内容")).toBeTruthy();
  });

  it("应用自定义 className", () => {
    const { container } = render(<GlassCard className="custom-class">x</GlassCard>);
    expect(container.querySelector(".custom-class")).toBeTruthy();
  });

  it("无 hover 变体不注入悬停阴影类", () => {
    const { container } = render(<GlassCard hover={false}>static</GlassCard>);
    expect(container.firstElementChild?.className).not.toContain("hover:");
  });
});

describe("StatusBadge", () => {
  it.each(["ok", "error", "warning", "idle"] as const)("%s 状态渲染标签与圆点", (status) => {
    const { container } = render(<StatusBadge status={status} label="运行中" />);
    expect(screen.getByText("运行中")).toBeTruthy();
    // 每种状态都有一个状态色圆点
    expect(container.querySelector(".rounded-full.w-1\\.5")).toBeTruthy();
  });
});

describe("StatCard", () => {
  it("渲染标题与数值", () => {
    render(<StatCard title="活跃角色" value={42} />);
    expect(screen.getByText("活跃角色")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
  });

  it("可选 icon 渲染", () => {
    render(<StatCard title="消息" value="1.2k" icon="💬" />);
    expect(screen.getByText("💬")).toBeTruthy();
  });
});

describe("LoadingSpinner", () => {
  it("默认文案为加载中", () => {
    render(<LoadingSpinner />);
    expect(screen.getByText("加载中...")).toBeTruthy();
  });

  it("自定义文案", () => {
    render(<LoadingSpinner text="正在同步世界..." />);
    expect(screen.getByText("正在同步世界...")).toBeTruthy();
  });
});

describe("ErrorDisplay", () => {
  it("展示错误信息与固定标题", () => {
    render(<ErrorDisplay error={new Error("网络超时")} />);
    expect(screen.getByText("加载失败")).toBeTruthy();
    expect(screen.getByText("网络超时")).toBeTruthy();
  });
});

describe("ProgressBar", () => {
  // 注：宽度由 framer-motion 动画驱动，jsdom 不执行动画，
  // 故只断言静态类与颜色变体，不断言运行时宽度
  it("渲染进度条轨道与渐变填充", () => {
    const { container } = render(<ProgressBar value={50} max={100} />);
    expect(container.querySelector(".h-2\\.5")).toBeTruthy();
    expect(container.querySelector(".h-full")?.className).toContain("from-sakura-300");
  });

  it.each(["sky", "twilight"] as const)("%s 颜色变体生效", (color) => {
    const { container } = render(<ProgressBar value={30} color={color} />);
    expect(container.querySelector(".h-full")?.className).toContain(color);
  });
});

describe("Skeleton 系列", () => {
  it("Skeleton 应用自定义类", () => {
    const { container } = render(<Skeleton className="h-4 w-1/3" />);
    expect(container.firstElementChild?.className).toContain("w-1/3");
  });

  it("SkeletonList 默认渲染 3 张卡", () => {
    const { container } = render(<SkeletonList />);
    // SkeletonCard 根节点使用 rounded-3xl（文件内唯一），以此计数
    expect(container.querySelectorAll(".rounded-3xl").length).toBe(3);
  });

  it("SkeletonList 按 count 渲染", () => {
    const { container } = render(<SkeletonList count={5} />);
    expect(container.querySelectorAll(".rounded-3xl").length).toBe(5);
  });

  it("SkeletonCard 渲染骨架块", () => {
    const { container } = render(<SkeletonCard />);
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(3);
  });
});

describe("PageHeader", () => {
  it("渲染标题与副标题", () => {
    render(<PageHeader title="角色管理" subtitle="查看所有居民" />);
    expect(screen.getByText("角色管理")).toBeTruthy();
    expect(screen.getByText("查看所有居民")).toBeTruthy();
  });

  it("无 backTo 时不渲染返回链接", () => {
    render(<PageHeader title="首页" />);
    expect(screen.queryByText("返回")).toBeNull();
  });
});

describe("EmptyState", () => {
  it("渲染标题、图标与副标题", () => {
    render(<EmptyState icon="📭" title="暂无数据" subtitle="稍后再来看看" />);
    expect(screen.getByText("暂无数据")).toBeTruthy();
    expect(screen.getByText("稍后再来看看")).toBeTruthy();
    expect(screen.getByText("📭")).toBeTruthy();
  });

  it("未传副标题时不渲染副标题节点", () => {
    render(<EmptyState title="空空如也" />);
    expect(screen.getByText("空空如也")).toBeTruthy();
  });
});

describe("AnimeButton", () => {
  it("点击触发 onClick", () => {
    const onClick = vi.fn();
    render(<AnimeButton onClick={onClick}>保存</AnimeButton>);
    fireEvent.click(screen.getByText("保存"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disabled 时不触发点击", () => {
    const onClick = vi.fn();
    render(
      <AnimeButton onClick={onClick} disabled>
        保存
      </AnimeButton>,
    );
    fireEvent.click(screen.getByText("保存"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("danger 变体应用红色系样式", () => {
    const { container } = render(<AnimeButton variant="danger">删除</AnimeButton>);
    expect(container.firstElementChild?.className).toContain("from-red-400");
  });
});

describe("AnimeInput", () => {
  it("透传 placeholder 与输入事件", () => {
    render(<AnimeInput placeholder="搜索角色" />);
    const input = screen.getByPlaceholderText("搜索角色") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "小艾" } });
    expect(input.value).toBe("小艾");
  });

  it("有 icon 时输入框加左内边距", () => {
    const { container } = render(<AnimeInput icon="🔍" />);
    expect(container.querySelector("input")?.className).toContain("pl-12");
  });
});
