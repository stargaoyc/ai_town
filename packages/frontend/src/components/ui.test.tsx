// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { GlassCard } from "./ui";

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
