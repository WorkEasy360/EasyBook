import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState, ForbiddenState } from "@/components/ui/states";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { reportGroupsForRole } from "@/features/reports/catalogue";

export const metadata: Metadata = { title: "Reports" };

/**
 * The report centre: every report the backend serves, grouped by the question
 * area it answers, filtered to what this role's API permissions allow. There
 * are no figures here — each report page asks the engine itself.
 */
export default async function ReportsPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_REPORTS)) {
    return (
      <>
        <PageHeader title="Reports" />
        <ForbiddenState resource="reports" />
      </>
    );
  }

  const groups = reportGroupsForRole(session.role);

  return (
    <>
      <PageHeader
        title="Reports"
        description="Every figure in these reports is computed by the accounting engine from posted records."
      />
      <PageBody>
        {groups.length === 0 ? (
          <div className="rounded-lg border border-ink-200 bg-white">
            <EmptyState
              title="No reports available to your role"
              description="Ask an administrator in your organization if you need access to a report."
            />
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {groups.map((group) => (
              <Card key={group.key}>
                <CardHeader title={group.label} />
                <ul className="divide-y divide-ink-100">
                  {group.entries.map((entry) => (
                    <li key={entry.href} className="px-4 py-2.5">
                      <Link href={appHref(entry.href)} className="text-sm font-medium text-brand-700 hover:underline">
                        {entry.title}
                      </Link>
                      <p className="mt-0.5 text-xs text-ink-500">{entry.description}</p>
                    </li>
                  ))}
                </ul>
                {group.key === "tax" ? (
                  <p className="border-t border-ink-100 px-4 py-2.5 text-xs text-ink-500">
                    These are figures only. e-Invoice, e-Way Bill and return filing are not available.
                  </p>
                ) : null}
              </Card>
            ))}
          </div>
        )}
      </PageBody>
    </>
  );
}
