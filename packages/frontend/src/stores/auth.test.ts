import { beforeEach, describe, expect, it, vi } from "vitest";

// localStorage 桩：node 环境无 DOM 存储，auth store 初始化与持久化依赖它
const storage = new Map<string, string>();
const localStorageStub = {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
};

vi.stubGlobal("localStorage", localStorageStub);

describe("useAuthStore", () => {
  beforeEach(() => {
    storage.clear();
    vi.resetModules();
  });

  it("初始状态：无 token 时未认证", async () => {
    const { useAuthStore } = await import("./auth");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("logout 清空凭证并落回未认证", async () => {
    const { useAuthStore } = await import("./auth");
    useAuthStore.setState({ token: "t", userId: "u", isAuthenticated: true });
    useAuthStore.getState().logout();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().token).toBeNull();
    expect(storage.get("token")).toBeUndefined();
  });

  it("login 成功时持久化 token 并置为已认证", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ token: "jwt-1", user_id: "admin" }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { useAuthStore } = await import("./auth");
    const result = await useAuthStore.getState().login("admin", "pw");

    expect(result.success).toBe(true);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().token).toBe("jwt-1");
    expect(storage.get("token")).toBe("jwt-1");
  });

  it("login 失败（非 2xx）返回错误且不写入凭证", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "Bad credentials" }), { status: 401 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { useAuthStore } = await import("./auth");
    const result = await useAuthStore.getState().login("admin", "wrong");

    expect(result.success).toBe(false);
    expect(result.error).toBe("Bad credentials");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
