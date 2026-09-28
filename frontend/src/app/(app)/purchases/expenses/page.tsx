import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { EXPENSE_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { indexList } from "@/lib/api/lookups";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Expense, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Expenses" };

const RESOURCE = "purchases/expenses";

export default async function ExpensesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_EXPENSES)) {
    return (
      <>
        <PageHeader title="Expenses" />
        <ForbiddenState resource="expenses" />
      </>
    );
  }

  const canCreate = roleHasPermission(session.role, PERMISSIONS.MANAGE_EXPENSES);
  const query = parseListQuery("/purchases/expenses", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<Expense>(RESOURCE, { query: query.apiParams }));

  const vendorFilter = query.filters["vendor"];
  const vendorIds = result.ok ? result.data.results.map((row) => row.vendor) : [];
  if (vendorFilter) vendorIds.push(vendorFilter);
  const vendors = roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)
    ? await indexList<Vendor>("purchases/vendors", vendorIds)
    : new Map<string, Vendor>();

  const columns: Column<Expense>[] = [
    {
      key: "expense_number",
      header: "Expense",
      cell: (row) => (row.expense_number ? <span className="tabular">{row.expense_number}</span> : <span className="italic">Draft</span>),
    },
    {
      key: "description",
      header: "Description",
      cell: (row) => (
        <span>
          {row.description || <span className="text-ink-400">No description</span>}
          {row.vendor ? (
            <span className="block text-xs text-ink-500">{vendors.get(row.vendor)?.display_name ?? "Vendor"}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "expense_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.expense_date)}</span>,
    },
    {
      key: "is_billable",
      header: "Billable",
      hideBelow: "md",
      cell: (row) =>
        row.is_billable ? (
          <Badge tone="brand" size="sm">
            Billable
          </Badge>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={EXPENSE_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, cell: (row) => <Money value={row.total} currency={row.currency} strong /> },
  ];

  return (
    <>
      <PageHeader
        title="Expenses"
        description="Direct costs with no items or stock. A draft is posted to the ledger in one step."
        actions={
          canCreate ? (
            <LinkButton href="/purchases/expenses/new" variant="primary">
              New expense
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            { key: "status", label: "Status", value: query.filters["status"] ?? "", options: statusOptions(EXPENSE_STATUS) },
            {
              key: "is_billable",
              label: "Billable",
              value: query.filters["is_billable"] ?? "",
              options: [
                { value: "true", label: "Billable" },
                { value: "false", label: "Not billable" },
              ],
            },
            ...(vendorFilter
              ? [
                  {
                    key: "vendor",
                    label: "Vendor",
                    value: vendorFilter,
                    options: [{ value: vendorFilter, label: vendors.get(vendorFilter)?.display_name ?? "Selected vendor" }],
                  },
                ]
              : []),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Expenses"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/purchases/expenses/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No expenses match these filters" : "No expenses yet"}
          emptyDescription="Record an expense for a cost paid without a bill, like fuel or office supplies."
          {...(canCreate ? { emptyAction: { label: "New expense", href: "/purchases/expenses/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
