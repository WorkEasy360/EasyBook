import Link from "next/link";

/**
 * 404. Deliberately does NOT bounce to the dashboard (spec §84) — a silent
 * redirect hides a broken link instead of reporting it.
 */
export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 py-16 text-center">
      <p className="text-2xs font-semibold tracking-widest text-ink-400 uppercase">Error 404</p>
      <h1 className="mt-2 text-lg font-semibold text-ink-900">Page not found</h1>
      <p className="mt-2 max-w-md text-sm text-ink-600">
        The page you were looking for does not exist, or the record has been removed.
      </p>
      <Link
        href="/dashboard"
        className="mt-6 inline-flex h-9 items-center rounded-md border border-brand-700 bg-brand-700 px-3.5 text-sm font-medium text-white hover:bg-brand-800"
      >
        Go to dashboard
      </Link>
    </div>
  );
}
