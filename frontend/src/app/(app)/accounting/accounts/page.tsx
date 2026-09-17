import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { ACCOUNT_TYPE_LABELS, labelOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { AccountDialogButton } from "@/features/accounting/account-dialog";
import { serverApi, tryServer } from "@/lib/api/server";
import { indexList } from "@/lib/api/lookups";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { Account } from "@/types/api/accounting";

export const metadata: Metadata = { title: "Chart of accounts" };

/**
 * The chart of accounts.
 *
 * The list endpoint is flat and ordered by code; hierarchy exists only as each
 * account's `parent` id. Parents are resolved to names (and their own parents
 * walked for depth) from one indexed page of accounts, so a sub-account reads
 * as "under 1000 · Bank" even when its parent is on another page.
 */

const ACTIVE_OPTIONS = [
  { value: "true", label: "Active" },
  { value: "false", label: "Inactive" },
];

/** Indentation per hierarchy level; deeper levels share the last step. */
const DEPTH_INDENT = ["", "pl-3", "pl-6", "pl-9", "pl-12"] as const;

/** Parent chain from nearest to root, stopping at a cycle or an unresolved id. */
function ancestorsOf(account: Account, accounts: Map<string, Account>): Account[] {
  const chain: Account[] = [];
  const seen = new Set<string>([account.id]);
  let parentId = account.parent;
  while (parentId && !seen.has(parentId)) {
    const parent = accounts.get(parentId);
    if (!parent) break;
    chain.push(parent);
    seen.add(parent.id);
    parentId = parent.parent;
  }
  return chain;
}

export default async function AccountsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_ACCOUNTING)) {
    return (
      <>
        <PageHeader title="Chart of accounts" />
        <ForbiddenState resource="the chart of accounts" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_ACCOUNTING);
  const query = parseListQuery("/accounting/accounts", "accounting/accounts", params);
  const result = await tryServer(() => serverApi.list<Account>("accounting/accounts", { query: query.apiParams }));

  const rows = result.ok ? result.data.results : [];
  const accounts = await indexList<Account>(
    "accounting/accounts",
    rows.map((row) => row.parent),
  );
  for (const row of rows) accounts.set(row.id, row);

  const columns: Column<Account>[] = [
    { key: "code", header: "Code", cell: (row) => <span className="tabular">{row.code}</span> },
    {
      key: "name",
      header: "Name",
      cell: (row) => {
        const ancestors = ancestorsOf(row, accounts);
        const parent = ancestors[0];
        return (
          <div className={DEPTH_INDENT[Math.min(ancestors.length, DEPTH_INDENT.length - 1)]}>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium text-ink-900">{row.name}</span>
              {row.is_system ? (
                <Badge tone="brand" size="sm">
                  System
                </Badge>
              ) : null}
            </div>
            {row.parent ? (
              <p className="text-xs text-ink-500">
                Under {parent ? `${parent.code} · ${parent.name}` : "another account"}
              </p>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "account_type",
      header: "Type",
      cell: (row) => ACCOUNT_TYPE_LABELS[row.account_type] ?? row.account_type,
    },
    {
      key: "account_subtype",
      header: "Subtype",
      hideBelow: "md",
      cell: (row) => row.account_subtype || <span className="text-ink-400">—</span>,
    },
    {
      key: "is_active",
      header: "Status",
      cell: (row) => (
        <Badge tone={row.is_active ? "success" : "neutral"} marker={row.is_active} size="sm">
          {row.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
  ];

  if (canManage) {
    columns.push({
      key: "actions",
      header: "",
      headerLabel: "Actions",
      numeric: true,
      cell: (row) => <AccountDialogButton account={row} />,
    });
  }

  return (
    <>
      <PageHeader
        title="Chart of accounts"
        description="Every posting lands on one of these accounts. Balances are derived from posted journals, never stored here."
        actions={
          <>
            <LinkButton href="/accounting/trial-balance">Trial balance</LinkButton>
            {canManage ? <AccountDialogButton /> : null}
          </>
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "account_type",
              label: "Type",
              value: query.filters["account_type"] ?? "",
              options: labelOptions(ACCOUNT_TYPE_LABELS),
            },
            {
              key: "is_active",
              label: "Status",
              value: query.filters["is_active"] ?? "",
              options: ACTIVE_OPTIONS,
            },
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Chart of accounts"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/accounting/accounts/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No accounts match these filters" : "No accounts yet"}
          emptyDescription={
            canManage
              ? "New organizations start with an empty chart. Add the accounts your documents will post to."
              : "An administrator needs to set up the chart of accounts."
          }
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("accounting/accounts").defaultOrder ?? "code"} />}
        />
      </PageBody>
    </>
  );
}
