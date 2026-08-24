// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AnimeBackground } from "./AnimeBackground";

afterEach(() => {
  cleanup();
});

describe("AnimeBackground", () => {
  it("渲染背景容器", () => {
    const { container } = render(<AnimeBackground />);
    expect(container.firstElementChild).not.toBeNull();
  });
});
