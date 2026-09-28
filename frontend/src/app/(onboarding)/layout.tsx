/**
 * Frame for signed-in pages that must work WITHOUT an organization.
 *
 * Deliberately outside the (app) group: that layout calls requireSession(),
 * which redirects a user with no organization to /onboarding/organization —
 * nesting this page there would redirect it to itself forever. There is no
 * org-scoped provider here either (no OrgProvider, no tenant query cache),
 * because there is no tenant yet. src/proxy.ts still requires a session
 * cookie for these routes.
 */
export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-dvh items-start justify-center bg-ink-50 px-4 py-10 sm:items-center">
      <div className="w-full max-w-xl">
        <div className="mb-6 flex items-center gap-2">
          <span
            aria-hidden="true"
            className="flex size-8 items-center justify-center rounded bg-brand-700 text-sm font-bold text-white"
          >
            E
          </span>
          <span className="text-lg font-semibold tracking-tight text-ink-900">EasyBook</span>
        </div>
        {children}
      </div>
    </main>
  );
}
