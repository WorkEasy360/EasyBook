"use client";

import * as React from "react";
import { cn } from "@/lib/cn";
import { Icons } from "./icons";

/**
 * Transient confirmations ("Invoice INV-1024 posted").
 *
 * Toasts confirm; they never REPORT a failure that the user must act on. A
 * rejected save belongs next to the form field or in the form-level alert,
 * where it stays put — a validation error that fades after four seconds is a
 * bug, not a notification.
 */

export type ToastTone = "success" | "info" | "warning" | "error";

export interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  description?: string;
  /** "Reference: …" for support when the toast reports a server failure. */
  reference?: string;
  /** Milliseconds. Errors default to staying until dismissed. */
  duration?: number;
}

interface ToastContextValue {
  push: (toast: Omit<Toast, "id">) => void;
  dismiss: (id: string) => void;
}

const ToastContext = React.createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<Toast[]>([]);
  const timers = React.useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = React.useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = React.useCallback(
    (toast: Omit<Toast, "id">) => {
      const id = crypto.randomUUID();
      setToasts((current) => [...current, { ...toast, id }]);

      // An error stays until dismissed: it usually carries a reference the
      // user may need to copy.
      const duration = toast.duration ?? (toast.tone === "error" ? 0 : 5000);
      if (duration > 0) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), duration),
        );
      }
    },
    [dismiss],
  );

  React.useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending.values()) clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const value = React.useMemo(() => ({ push, dismiss }), [push, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = React.useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider.");
  return context;
}

const TONES: Record<ToastTone, { container: string; icon: React.ReactNode }> = {
  success: {
    container: "border-success-100 bg-success-50 text-success-700",
    icon: <Icons.check className="size-4" />,
  },
  info: {
    container: "border-info-100 bg-info-50 text-info-700",
    icon: <Icons.inbox className="size-4" />,
  },
  warning: {
    container: "border-warning-100 bg-warning-50 text-warning-700",
    icon: <Icons.warning className="size-4" />,
  },
  error: {
    container: "border-danger-100 bg-danger-50 text-danger-700",
    icon: <Icons.warning className="size-4" />,
  },
};

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: string) => void;
}) {
  return (
    <div
      // A confirmation is polite; an error is assertive and interrupts. Two
      // regions because a live region's politeness cannot change per message.
      className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2"
      data-print="hide"
    >
      <div aria-live="polite" aria-atomic="false" className="flex flex-col gap-2">
        {toasts
          .filter((toast) => toast.tone !== "error")
          .map((toast) => (
            <ToastCard key={toast.id} toast={toast} onDismiss={onDismiss} />
          ))}
      </div>
      <div aria-live="assertive" aria-atomic="false" className="flex flex-col gap-2">
        {toasts
          .filter((toast) => toast.tone === "error")
          .map((toast) => (
            <ToastCard key={toast.id} toast={toast} onDismiss={onDismiss} />
          ))}
      </div>
    </div>
  );
}

function ToastCard({ toast, onDismiss }: { toast: Toast; onDismiss: (id: string) => void }) {
  const tone = TONES[toast.tone];
  return (
    <div
      className={cn(
        "animate-slide-up pointer-events-auto flex items-start gap-2.5 rounded-md border px-3 py-2.5 shadow-md",
        tone.container,
      )}
    >
      <span aria-hidden="true" className="mt-0.5 shrink-0">
        {tone.icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{toast.title}</p>
        {toast.description ? <p className="mt-0.5 text-xs opacity-90">{toast.description}</p> : null}
        {toast.reference ? (
          <p className="mt-1 font-mono text-2xs opacity-70">Reference: {toast.reference}</p>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss notification"
        className="shrink-0 rounded p-0.5 opacity-60 transition-opacity hover:opacity-100"
      >
        <Icons.close className="size-3.5" />
      </button>
    </div>
  );
}
