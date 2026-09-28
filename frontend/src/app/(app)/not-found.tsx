import Link from "next/link";

/**
 * 404 inside the app shell — a record that does not exist in THIS
 * organization (a foreign or deleted id) lands here from notFound(), with the
 * navigation still in place. Deliberately not a redirect to the dashboard: a
 * silent bounce hides a broken link instead of reporting it (spec §84).
 */
export default function AppNotFound() {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center px-6 py-16 text-center">
      <p className="text-2xs font-semibold tracking-widest text-ink-400 uppercase">Not found</p>
      <h1 className="mt-2 text-lg font-semibold text-ink-900">This record could not be found</h1>
      <p className="mt-2 max-w-md text-sm text-ink-600">
        It may have been removed, or it belongs to a different organization than the one you are working in.
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
