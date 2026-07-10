import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center p-4">
          <div className="bg-glass-bg backdrop-blur-glass-blur rounded-2xl p-8 shadow-soft max-w-md w-full text-center">
            <div className="text-5xl mb-4">😵</div>
            <h1 className="text-xl font-bold text-sakura-600 mb-2">
              页面出错了
            </h1>
            <p className="text-sm text-twilight-400 mb-4">
              {this.state.error?.message || "发生了未知错误"}
            </p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 rounded-lg bg-sakura-500 text-white text-sm font-medium hover:bg-sakura-600 transition-colors"
            >
              刷新页面
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
