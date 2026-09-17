"use client";

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

/**
 * Error boundary for authenticated pages.
 *
 * Without it an exception in any page bubbles to src/app/error.tsx, which sits
 * ABOVE this route group's layout — the sidebar and organization switcher
 * vanish and the user is left on a bare error screen. Here the shell stays,
 * so they can simply go somewhere else.
 *
 * Only the digest is shown, never the message: a server exception can carry
 * a query or a value that must not reach the browser (spec §84).
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    if (process.env.NODE_ENV !== "production") console.error(error);
  }, [error]);

  return (
    <div role="alert" className="flex min-h-[50vh] flex-col items-center justify-center px-6 py-16 text-center">
      <h1 className="text-lg font-semibold text-ink-900">This page could not be displayed</h1>
      <p className="mt-2 max-w-md text-sm text-ink-600">
        Nothing was saved or changed. Try again, or continue from another page.
      </p>
      {error.digest ? <p className="mt-3 font-mono text-2xs text-ink-400">Reference: {error.digest}</p> : null}
      <div className="mt-6 flex gap-2">
        <Button variant="primary" onClick={reset}>
          Try again
        </Button>
        <Link
          href="/dashboard"
          className="inline-flex h-9 items-center rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 hover:bg-ink-50"
        >
          Go to dashboard
        </Link>
      </div>
    </div>
  );
}
