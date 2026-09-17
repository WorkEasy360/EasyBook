import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { SettingsTabs } from "@/features/settings/tabs";
import { serverApi, tryServer } from "@/lib/api/server";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, ROLE_LABELS, roleHasPermission, type Role } from "@/lib/authz/permissions";
import { formatDateTime } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import { displayName, type Membership } from "@/types/api/accounts";

export const metadata: Metadata = { title: "Members" };

/**
 * GET organizations/members/ — a bare array (no pagination) of the ACTIVE
 * memberships of the organization. MembershipListView has no permission gate
 * beyond membership, so every role may view it.
 *
 * Inviting, removing and changing roles have no endpoint.
 * BACKEND CONTRACT BLOCKER: POST organizations/members/ (invite) and
 * PATCH/DELETE organizations/members/{id}/ (role change, removal), gated by
 * members.manage. The UI stops at this read-only list.
 */
export default async function MembersPage() {
  const session = await requireSession();
  const result = await tryServer(() => serverApi.get<Membership[]>("organizations/members"));
  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_MEMBERS);

  const columns: Column<Membership>[] = [
    {
      key: "name",
      header: "Name",
      cell: (row) => (
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-ink-900">{displayName(row.user)}</span>
          {row.user.id === session.user.id ? (
            <Badge tone="brand" size="sm">
              You
            </Badge>
          ) : null}
        </span>
      ),
    },
    { key: "email", header: "Email", hideBelow: "sm", cell: (row) => <span className="break-all">{row.user.email}</span> },
    { key: "role", header: "Role", cell: (row) => ROLE_LABELS[row.role as Role] ?? row.role },
    {
      key: "joined",
      header: "Member since",
      hideBelow: "md",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">{formatDateTime(row.created_at, { timeZone: session.timeZone })}</span>
      ),
    },
  ];

  const members = result.ok ? result.data : [];

  return (
    <>
      <PageHeader
        title="Members"
        description={`People with access to ${session.organization.name}. What each role can do is decided by the server.`}
      />
      <SettingsTabs />
      <PageBody>
        {canManage ? (
          <p role="note" className="rounded-md border border-ink-200 bg-ink-50 px-3 py-2 text-sm text-ink-600">
            Inviting members, changing roles and removing access are not available yet.
          </p>
        ) : null}
        <DataTable
          caption={`Members of ${session.organization.name}`}
          columns={columns}
          data={result.ok ? wholeList(members) : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          emptyTitle="No active members"
          page={1}
          pageSize={Math.max(members.length, 1)}
          buildPageHref={() => "/settings/members"}
        />
      </PageBody>
    </>
  );
}
