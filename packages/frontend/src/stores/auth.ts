import { create } from "zustand";

interface AuthState {
  token: string | null;
  userId: string | null;
  isAuthenticated: boolean;
  login: (
    username: string,
    password: string,
  ) => Promise<{ success: boolean; error?: string }>;
  logout: () => void;
  init: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem("token"),
  userId: localStorage.getItem("user_id"),
  isAuthenticated: !!localStorage.getItem("token"),

  login: async (username: string, password: string) => {
    try {
      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({ detail: "Login failed" }));
        return { success: false, error: data.detail || `HTTP ${res.status}` };
      }
      const data = await res.json();
      localStorage.setItem("token", data.token);
      localStorage.setItem("user_id", data.user_id);
      set({ token: data.token, userId: data.user_id, isAuthenticated: true });
      return { success: true };
    } catch {
      return { success: false, error: "网络错误，请检查后端服务是否启动" };
    }
  },

  logout: () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user_id");
    set({ token: null, userId: null, isAuthenticated: false });
  },

  init: () => {
    const token = localStorage.getItem("token");
    if (token) {
      set({
        token,
        userId: localStorage.getItem("user_id"),
        isAuthenticated: true,
      });
    }
  },
}));
