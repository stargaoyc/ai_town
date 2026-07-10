import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { motion } from "framer-motion";
import { User, Lock, Sparkles } from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { AnimeBackground } from "@/components/AnimeBackground";
import { AnimeButton } from "@/components/ui";

export const Route = createFileRoute("/login")({
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      setError("请输入账号和密码");
      return;
    }
    setLoading(true);
    setError("");
    const result = await login(username.trim(), password.trim());
    setLoading(false);
    if (result.success) {
      navigate({ to: "/" });
    } else {
      setError(result.error || "登录失败");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative">
      <AnimeBackground />
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5 }}
        className="relative z-10 w-full max-w-md"
      >
        <div className="bg-white/70 backdrop-blur-2xl rounded-[2rem] p-8 border border-white/60 shadow-[0_20px_60px_rgba(255,143,171,0.25)]">
          <div className="text-center mb-8">
            <motion.div
              className="inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-gradient-to-br from-sakura-400 to-twilight-400 mb-4 shadow-lg"
              animate={{ rotate: [0, 5, -5, 0] }}
              transition={{ duration: 4, repeat: Infinity }}
            >
              <Sparkles className="w-10 h-10 text-white" />
            </motion.div>
            <h1 className="text-3xl font-bold bg-gradient-to-r from-sakura-500 via-twilight-400 to-sky-soft-500 bg-clip-text text-transparent">
              AI Town
            </h1>
            <p className="text-sm text-twilight-400 mt-2">
              二次元 AI 小镇陪伴智能体
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="relative">
              <User className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-sakura-400" />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="账号"
                className="w-full pl-12 pr-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 placeholder:text-twilight-300 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent transition-all"
                autoFocus
              />
            </div>
            <div className="relative">
              <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-sakura-400" />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="密码"
                className="w-full pl-12 pr-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 placeholder:text-twilight-300 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent transition-all"
              />
            </div>

            {error && (
              <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="px-4 py-2.5 rounded-xl bg-red-50/90 border border-red-200/50 text-red-600 text-sm"
              >
                {error}
              </motion.div>
            )}

            <AnimeButton
              type="submit"
              disabled={loading}
              className="w-full py-3 text-base"
            >
              {loading ? "登录中..." : "登录小镇"}
            </AnimeButton>
          </form>

          <div className="mt-6 text-center text-xs text-twilight-400">
            默认账号:{" "}
            <code className="px-2 py-0.5 rounded-lg bg-white/50 text-sakura-500 font-medium">
              admin
            </code>
            {" / "}
            <code className="px-2 py-0.5 rounded-lg bg-white/50 text-sakura-500 font-medium">
              admin123
            </code>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
