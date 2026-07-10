import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { useAuthStore } from '@/stores/auth';

export const Route = createFileRoute('/login')({
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey.trim()) {
      setError('请输入 API Key');
      return;
    }
    setLoading(true);
    setError('');
    const result = await login(apiKey.trim());
    setLoading(false);
    if (result.success) {
      navigate({ to: '/' });
    } else {
      setError(result.error || '登录失败');
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative overflow-hidden">
      {/* 装饰性背景圆 */}
      <div className="absolute top-10 left-10 w-72 h-72 bg-sakura-300/30 rounded-full blur-3xl animate-pulse" />
      <div className="absolute bottom-10 right-10 w-96 h-96 bg-sky-soft-300/30 rounded-full blur-3xl animate-pulse" style={{ animationDelay: '1s' }} />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-64 h-64 bg-twilight-300/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: '2s' }} />

      <div className="relative z-10 w-full max-w-md">
        <div className="bg-glass-bg backdrop-blur-glass-blur rounded-3xl p-8 shadow-soft border border-white/40">
          {/* Logo 区域 */}
          <div className="text-center mb-8">
            <div className="inline-block text-6xl mb-3 animate-bounce">🌸</div>
            <h1 className="text-3xl font-bold bg-gradient-to-r from-sakura-500 via-twilight-400 to-sky-soft-400 bg-clip-text text-transparent">
              AI Town
            </h1>
            <p className="text-sm text-twilight-400 mt-2">二次元 AI 小镇陪伴智能体</p>
          </div>

          {/* 登录表单 */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-twilight-500 mb-1.5">
                API Key
              </label>
              <div className="relative">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="请输入 API Key"
                  className="w-full px-4 py-2.5 rounded-xl bg-white/50 border border-sakura-200/50 text-twilight-600 placeholder:text-twilight-300 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent transition-all"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={() => setShowKey(!showKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-twilight-400 hover:text-sakura-500 text-sm"
                >
                  {showKey ? '隐藏' : '显示'}
                </button>
              </div>
            </div>

            {error && (
              <div className="px-4 py-2.5 rounded-xl bg-red-50/80 border border-red-200/50 text-red-600 text-sm">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 rounded-xl bg-gradient-to-r from-sakura-400 to-sakura-500 text-white font-medium hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-lg shadow-sakura-400/30 hover:shadow-sakura-400/50 hover:scale-[1.02] active:scale-[0.98]"
            >
              {loading ? '登录中...' : '登录'}
            </button>
          </form>

          {/* 提示 */}
          <div className="mt-6 text-center text-xs text-twilight-400">
            默认 API Key: <code className="px-1.5 py-0.5 rounded bg-white/40 text-sakura-500">your-api-key</code>
          </div>
        </div>
      </div>
    </div>
  );
}
