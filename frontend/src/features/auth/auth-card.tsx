import type * as React from "react";

/** The signed-out frame shared by sign-in and sign-up. */
export function AuthCard({
  title,
  description,
  notice,
  noticeTone = "warning",
  footer,
  children,
}: {
  title: string;
  description: string;
  /** A status message above the form (session expired, account created). */
  notice?: React.ReactNode;
  noticeTone?: "warning" | "success";
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-ink-50 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <span
            aria-hidden="true"
            className="flex size-8 items-center justify-center rounded bg-brand-700 text-sm font-bold text-white"
          >
            E
          </span>
          <span className="text-lg font-semibold tracking-tight text-ink-900">EasyBook</span>
        </div>

        <div className="rounded-lg border border-ink-200 bg-white p-6 shadow-sm">
          <h1 className="text-base font-semibold text-ink-900">{title}</h1>
          <p className="mt-1 text-sm text-ink-500">{description}</p>

          {notice ? (
            <p
              role="status"
              className={
                noticeTone === "success"
                  ? "mt-4 rounded-md border border-success-100 bg-success-50 px-3 py-2 text-sm text-success-700"
                  : "mt-4 rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700"
              }
            >
              {notice}
            </p>
          ) : null}

          <div className="mt-5">{children}</div>
        </div>

        {footer ? <p className="mt-4 text-center text-sm text-ink-600">{footer}</p> : null}
      </div>
    </main>
  );
}
