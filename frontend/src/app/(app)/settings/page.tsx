import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { UnavailableSettingsCard } from "@/features/settings/unavailable";
import { SettingsTabs } from "@/features/settings/tabs";
import { monthName } from "@/features/settings/months";
import { requireSession } from "@/lib/auth/context";
import { ROLE_LABELS, type Role } from "@/lib/authz/permissions";
import { formatDateTime } from "@/lib/datetime";

export const metadata: Metadata = { title: "Settings" };

/**
 * Settings overview. Every role reaches this page (navigation.ts); each
 * section decides what that role may see.
 *
 * The organization shown is the active one from GET organizations/ — already
 * loaded by requireSession, so no second request. It is read-only because no
 * endpoint updates an organization (BACKEND CONTRACT BLOCKER:
 * PATCH organizations/{id}/).
 */
export default async function SettingsPage() {
  const session = await requireSession();
  const organization = session.organization;

  return (
    <>
      <PageHeader title="Settings" description="Your organization, its members and your own account." />
      <SettingsTabs />
      <PageBody>
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Organization profile" description="Read-only: organization details cannot be edited yet." />
            <CardBody>
              <DetailList
                items={[
                  { label: "Name", value: organization.name },
                  { label: "Legal name", value: organization.legal_name || "—" },
                  { label: "GSTIN", value: organization.gstin ? <span className="tabular">{organization.gstin}</span> : "Not registered" },
                  { label: "Base currency", value: organization.default_currency },
                  { label: "Time zone", value: organization.timezone },
                  { label: "Fiscal year starts", value: monthName(organization.fiscal_year_start_month) },
                  { label: "Your role", value: session.role ? (ROLE_LABELS[session.role as Role] ?? session.role) : "—" },
                  { label: "Created", value: formatDateTime(organization.created_at, { timeZone: session.timeZone }) },
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Sections" />
            <CardBody>
              <ul className="flex flex-col gap-3 text-sm">
                <li>
                  <Link href="/settings/members" className="font-medium text-brand-700 hover:underline">
                    Members
                  </Link>
                  <p className="text-ink-500">Who has access to {organization.name}, and their roles.</p>
                </li>
                <li>
                  <Link href="/settings/profile" className="font-medium text-brand-700 hover:underline">
                    Your profile
                  </Link>
                  <p className="text-ink-500">Your name, email and the organizations you belong to.</p>
                </li>
                <li>
                  <Link href="/onboarding/organization" className="font-medium text-brand-700 hover:underline">
                    Create another organization
                  </Link>
                  <p className="text-ink-500">Set up a separate set of books. You become its owner.</p>
                </li>
              </ul>
            </CardBody>
          </Card>
        </div>

        <UnavailableSettingsCard />
      </PageBody>
    </>
  );
}
