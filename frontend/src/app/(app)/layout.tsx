import { requireSession } from "@/lib/auth/context";
import { navigationForRole } from "@/lib/navigation";
import { AppShell } from "@/components/shell/app-shell";
import { QueryProvider } from "@/components/providers/query-provider";
import { OrgProvider } from "@/components/providers/org-provider";
import { ToastProvider } from "@/components/ui/toast";

/**
 * Layout for every authenticated route.
 *
 * Session resolution happens here, on the server, so a protected page never
 * renders — not even for a frame — before auth is settled (spec §12). The
 * navigation is filtered by role server-side, so entries the user cannot
 * reach are never serialized to the browser at all.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  const sections = navigationForRole(session.role);

  return (
    // key: remounting on organization change throws away the previous
    // tenant's query cache entirely (spec §100).
    <QueryProvider key={session.organization.id}>
      <OrgProvider
        value={{
          organizationId: session.organization.id,
          organizationName: session.organization.name,
          role: session.role,
          currency: session.currency,
          timeZone: session.timeZone,
          locale: "en-IN",
          fiscalYearStartMonth: session.organization.fiscal_year_start_month,
        }}
      >
        <ToastProvider>
          <AppShell
            sections={sections}
            user={session.user}
            organizations={session.organizations}
            organization={session.organization}
          >
            {children}
          </AppShell>
        </ToastProvider>
      </OrgProvider>
    </QueryProvider>
  );
}
