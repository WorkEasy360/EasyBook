import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { BANK_ACCOUNT_KIND_LABELS, labelOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, indexList } from "@/lib/api/lookups";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { Account } from "@/types/api/accounting";
import type { BankAccount } from "@/types/api/banking";

export const metadata: Metadata = { title: "Bank accounts" };

/**
 * Bank and credit card accounts. Balances are not listed here: each one is a
 * server computation (banking/selectors.py) with its own summary endpoint, and
 * the account page shows all three side by side.
 */
export default async function BankAccountsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_BANK_ACCOUNTS)) {
    return (
      <>
        <PageHeader title="Bank accounts" />
        <ForbiddenState resource="bank accounts" />
      </>
    );
  }

  const canCreate = roleHasPermission(session.role, PERMISSIONS.MANAGE_BANK_ACCOUNTS);
  const query = parseListQuery("/banking/accounts", "bank-accounts", params);
  const result = await tryServer(() => serverApi.list<BankAccount>("bank-accounts", { query: query.apiParams }));

  const ledgerAccounts = roleHasPermission(session.role, PERMISSIONS.VIEW_ACCOUNTING)
    ? await indexList<Account>("accounting/accounts", result.ok ? result.data.results.map((row) => row.account) : [])
    : new Map<string, Account>();

  const columns: Column<BankAccount>[] = [
    {
      key: "name",
      header: "Account",
      cell: (row) => (
        <span>
          <span className="font-medium">{row.name}</span>
          {row.bank_name ? <span className="block text-xs text-ink-500">{row.bank_name}</span> : null}
        </span>
      ),
    },
    {
      key: "kind",
      header: "Kind",
      hideBelow: "sm",
      cell: (row) => BANK_ACCOUNT_KIND_LABELS[row.kind] ?? row.kind,
    },
    {
      key: "masked_number",
      header: "Number",
      hideBelow: "md",
      cell: (row) => (row.masked_number ? <span className="tabular">{row.masked_number}</span> : <span className="text-ink-400">—</span>),
    },
    {
      key: "account",
      header: "Ledger account",
      hideBelow: "lg",
      cell: (row) => accountLabel(ledgerAccounts, row.account),
    },
    { key: "currency", header: "Currency", hideBelow: "md", cell: (row) => row.currency },
    {
      key: "is_active",
      header: "Status",
      cell: (row) =>
        row.is_active ? (
          <Badge tone="success" size="sm" marker>
            Active
          </Badge>
        ) : (
          <Badge tone="neutral" size="sm">
            Inactive
          </Badge>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Bank accounts"
        description="Each bank or card account is paired with one ledger account. Statement lines are imported against it and reconciled to the books."
        actions={
          canCreate ? (
            <LinkButton href="/banking/accounts/new" variant="primary">
              New bank account
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "is_active",
              label: "Status",
              value: query.filters["is_active"] ?? "",
              options: [
                { value: "true", label: "Active" },
                { value: "false", label: "Inactive" },
              ],
            },
            {
              key: "kind",
              label: "Kind",
              value: query.filters["kind"] ?? "",
              options: labelOptions(BANK_ACCOUNT_KIND_LABELS),
            },
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Bank accounts"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/banking/accounts/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No bank accounts match these filters" : "No bank accounts yet"}
          emptyDescription="Add a bank or credit card account to import statements and reconcile them."
          {...(canCreate ? { emptyAction: { label: "New bank account", href: "/banking/accounts/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("bank-accounts").defaultOrder ?? "name"} />}
        />
      </PageBody>
    </>
  );
}
