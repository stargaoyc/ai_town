import { describe, expect, it } from "vitest";
import { queryKeys } from "./queries";

// queryKeys 是 TanStack Query 缓存失效的契约：结构被 useDashboardSocket 的
// invalidateQueries 依赖，形状漂移会导致实时推送静默失效
describe("queryKeys", () => {
  it("静态 key 返回常量元组", () => {
    expect(queryKeys.health).toEqual(["health"]);
    expect(queryKeys.world).toEqual(["world"]);
    expect(queryKeys.conversations).toEqual(["conversations"]);
  });

  it("参数化 key 稳定且区分参数", () => {
    expect(queryKeys.character("abc")).toEqual(["character", "abc"]);
    expect(queryKeys.characters({ active_only: true })).toEqual([
      "characters",
      { active_only: true },
    ]);
    expect(queryKeys.characters()).toEqual(["characters", undefined]);
    expect(queryKeys.messages("c1")).toEqual(["messages", "c1"]);
  });
});
