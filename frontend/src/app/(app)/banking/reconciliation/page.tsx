import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { BANK_RECONCILIATION_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { BankAccount, BankReconciliation } from "@/types/api/banking";

export const metadata: Metadata = { title: "Reconciliation" };

export default async function ReconciliationsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  // Listing needs VIEW_BANK_TRANSACTIONS (BankReconciliationListCreateView);
  // starting and closing one needs RECONCILE_BANK.
  if (!roleHasPermission(role, PERMISSIONS.VIEW_BANK_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Reconciliation" />
        <ForbiddenState resource="bank reconciliations" />
      </>
    );
  }

  const canReconcile = roleHasPermission(role, PERMISSIONS.RECONCILE_BANK);
  const query = parseListQuery("/banking/reconciliation", "bank-reconciliations", params);
  const [result, accounts] = await Promise.all([
    tryServer(() => serverApi.list<BankReconciliation>("bank-reconciliations", { query: query.apiParams })),
    roleHasPermission(role, PERMISSIONS.VIEW_BANK_ACCOUNTS)
      ? tryServer(() => serverApi.list<BankAccount>("bank-accounts", { query: { page_size: 200 } }))
      : Promise.resolve(null),
  ]);
  const accountList = accounts?.ok ? accounts.data.results : [];
  const accountById = new Map(accountList.map((row) => [row.id, row]));
  const filtered = query.filters["bank_account"];
  const accountOptions = accountList.map((row) => ({ value: row.id, label: row.name }));
  if (filtered && !accountById.has(filtered)) accountOptions.push({ value: filtered, label: "Selected account" });

  const currencyOf = (row: BankReconciliation) => {
    const account = accountById.get(row.bank_account);
    return account ? { currency: account.currency } : {};
  };

  const columns: Column<BankReconciliation>[] = [
    {
      key: "period",
      header: "Statement period",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">
          {formatDate(row.statement_start_date)} – {formatDate(row.statement_end_date)}
        </span>
      ),
    },
    {
      key: "bank_account",
      header: "Account",
      cell: (row) => accountById.get(row.bank_account)?.name ?? <span className="text-ink-400">Bank account</span>,
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={BANK_RECONCILIATION_STATUS} size="sm" /> },
    {
      key: "statement_closing_balance",
      header: "Statement closing balance",
      numeric: true,
      cell: (row) => <Money value={row.statement_closing_balance} {...currencyOf(row)} />,
    },
    {
      key: "cleared_balance",
      header: "Certified cleared balance",
      numeric: true,
      hideBelow: "md",
      // Frozen by the server on completion; null until then.
      cell: (row) =>
        row.cleared_balance !== null ? (
          <Money value={row.cleared_balance} {...currencyOf(row)} />
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    {
      key: "completed_at",
      header: "Completed",
      hideBelow: "lg",
      cell: (row) => (row.completed_at ? formatDateTime(row.completed_at, { timeZone: session.timeZone }) : <span className="text-ink-400">—</span>),
    },
  ];

  const newHref = filtered ? `/banking/reconciliation/new?bank_account=${filtered}` : "/banking/reconciliation/new";

  return (
    <>
      <PageHeader
        title="Reconciliation"
        description="Sign off a statement period once every line in it is explained and the cleared balance equals the bank's closing balance."
        actions={
          canReconcile ? (
            <LinkButton href={newHref} variant="primary">
              Start reconciliation
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        {accountOptions.length > 0 ? (
          <FilterBar
            groups={[
              {
                key: "bank_account",
                label: "Account",
                value: filtered ?? "",
                allLabel: "All accounts",
                options: accountOptions,
              },
            ]}
            buildFilterHref={query.buildFilterHref}
            clearHref={query.clearHref}
            activeFilterCount={query.activeFilterCount}
          />
        ) : null}
        <DataTable
          caption="Bank reconciliations"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/banking/reconciliation/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No reconciliations for this account" : "No reconciliations yet"}
          emptyDescription="Start one with a statement period and the closing balance printed on the statement."
          {...(canReconcile ? { emptyAction: { label: "Start reconciliation", href: newHref } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("bank-reconciliations").defaultOrder ?? "statement end date"} />}
        />
      </PageBody>
    </>
  );
}
