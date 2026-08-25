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

  it("角色域扩展 key 与迁移前字面量一致", () => {
    expect(queryKeys.reflections("c1")).toEqual(["reflections", "c1"]);
    expect(queryKeys.plans("c1")).toEqual(["plans", "c1"]);
    expect(queryKeys.characterActions("c1", 50)).toEqual(["characterActions", "c1", 50]);
    expect(queryKeys.stateHistory("c1", 50)).toEqual(["stateHistory", "c1", 50]);
    expect(queryKeys.nearbyCharacters("c1")).toEqual(["nearbyCharacters", "c1"]);
    expect(queryKeys.relations("c1")).toEqual(["relations", "c1"]);
    expect(queryKeys.personMemory("c1", "u1")).toEqual(["personMemory", "c1", "u1"]);
    expect(queryKeys.personMemoriesList("c1", 50)).toEqual(["personMemoriesList", "c1", 50]);
  });

  it("日记 key：列表含 params 占位，锚点为两元素前缀", () => {
    expect(queryKeys.diaries("c1")).toEqual(["diaries", "c1", undefined]);
    expect(queryKeys.diaries("c1", { period: "day" })).toEqual([
      "diaries",
      "c1",
      { period: "day" },
    ]);
    expect(queryKeys.diariesByCharacter("c1")).toEqual(["diaries", "c1"]);
  });

  it("静态扩展 key 保持常量", () => {
    expect(queryKeys.config).toEqual(["config"]);
    expect(queryKeys.modules).toEqual(["modules"]);
    expect(queryKeys.detailedMetrics).toEqual(["detailedMetrics"]);
    expect(queryKeys.mcpServers).toEqual(["mcpServers"]);
    expect(queryKeys.mcpTools).toEqual(["mcpTools"]);
    expect(queryKeys.mcpServersHealth).toEqual(["mcpServersHealth"]);
  });

  it("运维与通知 key 区分 limit/level 参数", () => {
    expect(queryKeys.logs(100)).toEqual(["logs", 100, undefined]);
    expect(queryKeys.logs(100, "error")).toEqual(["logs", 100, "error"]);
    expect(queryKeys.onebotMessages(50)).toEqual(["onebotMessages", 50]);
    expect(queryKeys.proactiveShares(50)).toEqual(["proactiveShares", 50]);
    expect(queryKeys.worldSnapshots(20)).toEqual(["worldSnapshots", 20]);
    expect(queryKeys.notifications(50)).toEqual(["notifications", 50]);
    expect(queryKeys.worldEvents({ start_tick: 1 })).toEqual(["worldEvents", { start_tick: 1 }]);
    expect(queryKeys.messageStats()).toEqual(["messageStats", undefined]);
  });

  it("前缀失效锚点必须比参数化 key 少一层元素", () => {
    expect(queryKeys.charactersAll).toEqual(["characters"]);
    expect(queryKeys.notificationsAll).toEqual(["notifications"]);
  });
});
