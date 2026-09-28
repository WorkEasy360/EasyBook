import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { FiscalYearForm } from "@/features/onboarding/fiscal-year-form";
import { suggestedFiscalYear } from "@/features/onboarding/fiscal-year";
import { isValidTimeZone } from "@/features/onboarding/time-zone";
import { serverApi, tryServer } from "@/lib/api/server";
import { readSession } from "@/lib/auth/session";
import { todayInZone } from "@/lib/datetime";
import type { Organization } from "@/types/api/accounts";
import type { FiscalYearSetupStatus } from "@/types/api/accounting";

export const metadata: Metadata = { title: "Set up your fiscal year" };

/**
 * Required onboarding step after organization creation: requireSession()
 * sends every authenticated page here while the active organization has no
 * fiscal year covering today, because until one exists nothing can be posted.
 *
 * Like the organization step, it must NOT call requireSession() — that is
 * what redirects here. It resolves the active organization the same way
 * (session cookie, else the first membership) and asks the API, which is the
 * authority, whether setup is still needed.
 */
export default async function OnboardingFiscalYearPage() {
  const session = await readSession();
  if (!session) redirect("/login?next=/onboarding/fiscal-year");

  const organizations = await tryServer(() => serverApi.get<Organization[]>("organizations"));
  if (!organizations.ok) {
    throw new Error("EasyBook could not load your organizations. Please try again in a moment.");
  }
  const active =
    organizations.data.find((organization) => organization.id === session.organizationId) ?? organizations.data[0];
  if (!active) redirect("/onboarding/organization");

  const status = await tryServer(() => serverApi.get<FiscalYearSetupStatus>("accounting/fiscal-years/setup-status"));
  if (!status.ok) {
    throw new Error("EasyBook could not load your organization's setup. Please try again in a moment.");
  }
  if (status.data.has_current_fiscal_year) redirect("/dashboard");

  const today = todayInZone(isValidTimeZone(active.timezone) ? active.timezone : "UTC");
  const suggestion = suggestedFiscalYear(today, status.data.fiscal_year_start_month);

  return (
    <div className="rounded-lg border border-ink-200 bg-white p-6 shadow-sm">
      <h1 className="text-base font-semibold text-ink-900">Set up the fiscal year for {active.name}</h1>
      <p className="mt-1 text-sm text-ink-500">
        Every transaction is posted into a fiscal year. Confirm the dates of the one you are in now; you can add the
        following years later.
      </p>
      <div className="mt-5">
        {status.data.can_manage ? (
          <FiscalYearForm today={today} suggestion={suggestion} />
        ) : (
          <p role="note" className="rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700">
            {active.name} does not have a fiscal year for today yet, and your role cannot create one. Ask an owner,
            admin or accountant of this organization to sign in and complete this step.
          </p>
        )}
      </div>
    </div>
  );
}
