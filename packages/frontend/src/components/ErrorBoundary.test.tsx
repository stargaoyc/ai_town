// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Bomb({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error("boom");
  return <p>safe content</p>;
}

describe("ErrorBoundary", () => {
  // React 会在测试中打印捕获的错误，静音以保持输出干净
  vi.spyOn(console, "error").mockImplementation(() => {});

  it("正常子组件直接渲染", () => {
    render(
      <ErrorBoundary>
        <Bomb shouldThrow={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("safe content")).toBeTruthy();
  });

  it("子组件抛错时渲染兜底 UI 而非白屏", () => {
    render(
      <ErrorBoundary>
        <Bomb shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(screen.queryByText("safe content")).toBeNull();
    expect(screen.getByText("页面出错了")).toBeTruthy();
    expect(screen.getByText("刷新页面")).toBeTruthy();
  });

  it("兜底 UI 展示错误信息", () => {
    render(
      <ErrorBoundary>
        <Bomb shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/boom/)).toBeTruthy();
  });
});
