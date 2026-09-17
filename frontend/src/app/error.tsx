"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";

/**
 * Root error boundary (spec §84).
 *
 * Shows a recoverable message and the digest Next assigns to the underlying
 * error, never the error text itself — a server exception message can carry
 * a query, a path or a value that should not reach the browser.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    // Keeps the browser console useful in development without shipping the
    // message into the DOM in production.
    if (process.env.NODE_ENV !== "production") console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 py-16 text-center">
      <h1 className="text-lg font-semibold text-ink-900">Something went wrong</h1>
      <p className="mt-2 max-w-md text-sm text-ink-600">
        This page could not be displayed. Your data has not been changed.
      </p>
      {error.digest ? (
        <p className="mt-3 font-mono text-2xs text-ink-400">Reference: {error.digest}</p>
      ) : null}
      <div className="mt-6 flex gap-2">
        <Button variant="primary" onClick={reset}>
          Try again
        </Button>
        <a
          href="/dashboard"
          className="inline-flex h-9 items-center rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 hover:bg-ink-50"
        >
          Go to dashboard
        </a>
      </div>
    </div>
  );
}
