import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { SettingsTabs } from "@/features/settings/tabs";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { ROLE_LABELS, type Role } from "@/lib/authz/permissions";
import { displayName, type Organization } from "@/types/api/accounts";

export const metadata: Metadata = { title: "Your profile" };

/**
 * The signed-in user from GET auth/me/ and their organizations from
 * GET organizations/ — both already loaded by requireSession.
 *
 * Read-only: UserSerializer is entirely read-only and there is no profile or
 * password endpoint. BACKEND CONTRACT BLOCKER: PATCH auth/me/ and a
 * password-change endpoint.
 */
export default async function ProfilePage() {
  const session = await requireSession();
  const user = session.user;

  const columns: Column<Organization>[] = [
    {
      key: "name",
      header: "Organization",
      cell: (row) => (
        <span className="flex flex-col">
          <span className="font-medium text-ink-900">{row.name}</span>
          {row.id === session.organization.id ? <span className="text-xs text-ink-500">Currently open</span> : null}
        </span>
      ),
    },
    { key: "role", header: "Your role", cell: (row) => (row.role ? (ROLE_LABELS[row.role as Role] ?? row.role) : "—") },
    { key: "currency", header: "Currency", hideBelow: "sm", cell: (row) => row.default_currency },
  ];

  return (
    <>
      <PageHeader title="Your profile" description="Your details cannot be edited from the app yet." />
      <SettingsTabs />
      <PageBody>
        <Card>
          <CardHeader title="Account" />
          <CardBody>
            <DetailList
              items={[
                { label: "Name", value: displayName(user) },
                { label: "Email", value: <span className="break-all">{user.email}</span> },
                { label: "First name", value: user.first_name || "—" },
                { label: "Last name", value: user.last_name || "—" },
              ]}
            />
          </CardBody>
        </Card>
        <DataTable
          caption="Your organizations"
          columns={columns}
          data={wholeList(session.organizations)}
          getRowId={(row) => row.id}
          emptyTitle="No organizations"
          page={1}
          pageSize={Math.max(session.organizations.length, 1)}
          buildPageHref={() => "/settings/profile"}
        />
      </PageBody>
    </>
  );
}
