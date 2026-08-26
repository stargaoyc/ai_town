import { useEffect, useId, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useToastStore } from "@/stores/toast";
import type { ToastItem } from "@/stores/toast";
import { AnimeButton, GlassCard } from "./primitives";

/* =========================================================
   LoadingSpinner — 加载动画
   ========================================================= */

export function LoadingSpinner({ text = "加载中..." }: { text?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <motion.div
        className="relative w-14 h-14"
        animate={{ rotate: 360 }}
        transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
      >
        <div className="absolute inset-0 rounded-full border-4 border-sakura-200/60" />
        <div className="absolute inset-0 rounded-full border-4 border-transparent border-t-sakura-500" />
        <motion.div
          className="absolute inset-2 rounded-full bg-gradient-to-br from-sakura-300/40 to-sakura-400/20 blur-sm"
          animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0.8, 0.5] }}
          transition={{ duration: 2, repeat: Infinity }}
        />
      </motion.div>
      <span className="text-twilight-400 text-sm animate-pulse">{text}</span>
    </div>
  );
}

/* =========================================================
   ErrorDisplay — 错误显示
   ========================================================= */

export function ErrorDisplay({ error }: { error: Error }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="p-5 rounded-2xl bg-red-50/80 border border-red-200/50 backdrop-blur-sm shadow-soft"
    >
      <div className="flex items-center gap-2 text-red-600 font-semibold">
        <span className="text-lg">⚠️</span>
        <span>加载失败</span>
      </div>
      <div className="text-sm text-red-500 mt-1 ml-7">{error.message}</div>
    </motion.div>
  );
}
/* =========================================================
   EmptyState — 空状态
   ========================================================= */

export function EmptyState({
  icon = "📭",
  title,
  subtitle,
}: {
  icon?: string;
  title: string;
  subtitle?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="flex flex-col items-center justify-center py-14 text-center"
    >
      <motion.div
        className="text-5xl mb-3 opacity-70"
        animate={{ y: [0, -6, 0] }}
        transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
      >
        {icon}
      </motion.div>
      <div className="text-twilight-500 font-semibold text-lg">{title}</div>
      {subtitle && (
        <div className="text-sm text-twilight-400 mt-1 max-w-xs mx-auto">{subtitle}</div>
      )}
    </motion.div>
  );
}
/* =========================================================
   Toaster — 全局 toast 渲染器（消费 stores/toast）
   ========================================================= */

const toasterStyles: Record<ToastItem["kind"], { accent: string; icon: string }> = {
  success: {
    accent: "border-emerald-200/60 bg-emerald-50/90 text-emerald-700",
    icon: "✅",
  },
  error: {
    accent: "border-red-200/60 bg-red-50/90 text-red-600",
    icon: "⚠️",
  },
  info: {
    accent: "border-white/60 bg-white/80 text-twilight-600",
    icon: "💬",
  },
};

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <div
      aria-live="polite"
      className="fixed top-4 right-4 z-[100] flex flex-col items-end gap-2 pointer-events-none"
    >
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.button
            key={t.id}
            type="button"
            initial={{ opacity: 0, x: 40 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 40 }}
            transition={{ type: "spring", stiffness: 300, damping: 24 }}
            onClick={() => dismiss(t.id)}
            title="点击关闭"
            className={`pointer-events-auto flex items-center gap-2 max-w-xs px-4 py-2.5 rounded-xl border backdrop-blur-xl shadow-soft text-sm font-medium text-left ${toasterStyles[t.kind].accent}`}
          >
            <span aria-hidden>{toasterStyles[t.kind].icon}</span>
            <span>{t.message}</span>
          </motion.button>
        ))}
      </AnimatePresence>
    </div>
  );
}

/* =========================================================
   ConfirmDialog — 危险操作确认对话框
   ========================================================= */

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText,
  danger = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmText: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handleKeyDown);
    confirmRef.current?.focus();
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onCancel]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-[90] flex items-center justify-center bg-black/30 backdrop-blur-sm p-4"
          onClick={onCancel}
        >
          <motion.div
            initial={{ scale: 0.9, y: 20, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.9, y: 20, opacity: 0 }}
            transition={{ type: "spring", stiffness: 300, damping: 24 }}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-sm"
          >
            <GlassCard hover={false}>
              <h3
                id={titleId}
                className={`font-semibold text-lg ${danger ? "text-red-600" : "text-sakura-600"}`}
              >
                {title}
              </h3>
              {description && (
                <p className="text-sm text-twilight-400 mt-1 leading-relaxed">{description}</p>
              )}
              <div className="flex items-center justify-end gap-2 mt-5">
                <AnimeButton
                  variant="secondary"
                  onClick={onCancel}
                  className="!px-4 !py-2 !text-sm"
                >
                  取消
                </AnimeButton>
                <AnimeButton
                  ref={confirmRef}
                  variant={danger ? "danger" : "primary"}
                  onClick={onConfirm}
                  className="!px-4 !py-2 !text-sm"
                >
                  {confirmText}
                </AnimeButton>
              </div>
            </GlassCard>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
