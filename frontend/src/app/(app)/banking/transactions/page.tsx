import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable } from "@/components/ui/data-table";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { BANK_TRANSACTION_STATUS, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { transactionColumns } from "@/features/banking/transaction-columns";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { dateParamOf, parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { BankAccount, BankTransaction } from "@/types/api/banking";

export const metadata: Metadata = { title: "Bank transactions" };

/**
 * Statement lines across bank accounts — the reconciliation work queue.
 *
 * Filters are exactly what BankTransactionListView reads: bank_account,
 * status, from_date and to_date. The dates go through a GET form so a filtered
 * view stays a shareable URL.
 */
export default async function BankTransactionsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_BANK_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Bank transactions" />
        <ForbiddenState resource="bank transactions" />
      </>
    );
  }

  // Malformed dates are dropped before they reach the API.
  const cleaned: RawSearchParams = {
    ...params,
    from_date: dateParamOf(params, "from_date"),
    to_date: dateParamOf(params, "to_date"),
  };
  const query = parseListQuery("/banking/transactions", "bank-transactions", cleaned);

  const [result, accounts] = await Promise.all([
    tryServer(() => serverApi.list<BankTransaction>("bank-transactions", { query: query.apiParams })),
    roleHasPermission(session.role, PERMISSIONS.VIEW_BANK_ACCOUNTS)
      ? tryServer(() => serverApi.list<BankAccount>("bank-accounts", { query: { page_size: 200 } }))
      : Promise.resolve(null),
  ]);

  const accountList = accounts?.ok ? accounts.data.results : [];
  const accountById = new Map(accountList.map((row) => [row.id, row]));
  const selectedAccount = query.filters["bank_account"] ? accountById.get(query.filters["bank_account"]) : undefined;
  const fromDate = query.filters["from_date"];
  const toDate = query.filters["to_date"];
  const canImport = roleHasPermission(session.role, PERMISSIONS.IMPORT_BANK_STATEMENT);

  const accountOptions = accountList.map((row) => ({ value: row.id, label: row.name }));
  if (query.filters["bank_account"] && !selectedAccount) {
    accountOptions.push({ value: query.filters["bank_account"], label: "Selected account" });
  }

  // Clearing the dates keeps the account and status chips.
  const kept = new URLSearchParams();
  for (const key of ["bank_account", "status"]) {
    const value = query.filters[key];
    if (value) kept.set(key, value);
  }
  const clearDatesHref = kept.size > 0 ? `/banking/transactions?${kept.toString()}` : "/banking/transactions";

  const period =
    fromDate && toDate
      ? `${formatDate(fromDate)} – ${formatDate(toDate)}`
      : fromDate
        ? `from ${formatDate(fromDate)}`
        : toDate
          ? `up to ${formatDate(toDate)}`
          : null;

  return (
    <>
      <PageHeader
        title="Bank transactions"
        description={
          selectedAccount
            ? `Statement lines for ${selectedAccount.name}${period ? `, ${period}` : ""}.`
            : "Statement lines to explain: match each to a recorded document, categorize it, or exclude it if it is not a real transaction."
        }
        actions={
          canImport && selectedAccount?.is_active ? (
            <LinkButton href={`/banking/accounts/${selectedAccount.id}/import`} variant="primary">
              Import statement
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <div className="flex flex-col gap-3">
          <FilterBar
            groups={[
              {
                key: "status",
                label: "Status",
                value: query.filters["status"] ?? "",
                options: statusOptions(BANK_TRANSACTION_STATUS),
              },
              ...(accountOptions.length > 0
                ? [
                    {
                      key: "bank_account",
                      label: "Account",
                      value: query.filters["bank_account"] ?? "",
                      allLabel: "All accounts",
                      options: accountOptions,
                    },
                  ]
                : []),
            ]}
            buildFilterHref={query.buildFilterHref}
            clearHref={query.clearHref}
            activeFilterCount={query.activeFilterCount}
          />
          <DateFilterForm
            action="/banking/transactions"
            fields={[
              { name: "from_date", label: "From", value: fromDate },
              { name: "to_date", label: "To", value: toDate },
            ]}
            preserve={{ bank_account: query.filters["bank_account"], status: query.filters["status"] }}
            {...(fromDate || toDate ? { clearHref: clearDatesHref } : {})}
          />
        </div>
        <DataTable
          caption="Bank transactions"
          columns={transactionColumns({ accounts: accountById, showAccount: !selectedAccount })}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/banking/transactions/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No statement lines match these filters" : "No statement lines yet"}
          emptyDescription="Statement lines arrive by importing a CSV statement on a bank account."
          {...(accountList.length === 0 && roleHasPermission(session.role, PERMISSIONS.MANAGE_BANK_ACCOUNTS)
            ? { emptyAction: { label: "New bank account", href: "/banking/accounts/new" } }
            : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("bank-transactions").defaultOrder ?? "transaction date"} />}
        />
      </PageBody>
    </>
  );
}
