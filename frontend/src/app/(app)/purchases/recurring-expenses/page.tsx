import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { RECURRING_FREQUENCY_LABELS } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { indexList } from "@/lib/api/lookups";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { RecurringExpenseTemplate, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Recurring expenses" };

const RESOURCE = "purchases/recurring-expenses";

export default async function RecurringExpensesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  // The backend guards recurring expenses with the recurring-BILLS permissions
  // (RecurringExpenseTemplateListCreateView) — mirrored here, not invented.
  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_RECURRING_BILLS)) {
    return (
      <>
        <PageHeader title="Recurring expenses" />
        <ForbiddenState resource="recurring expenses" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_RECURRING_BILLS);
  const query = parseListQuery("/purchases/recurring-expenses", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<RecurringExpenseTemplate>(RESOURCE, { query: query.apiParams }));

  const vendorFilter = query.filters["vendor"];
  const vendorIds = result.ok ? result.data.results.map((row) => row.vendor) : [];
  if (vendorFilter) vendorIds.push(vendorFilter);
  const vendors = roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)
    ? await indexList<Vendor>("purchases/vendors", vendorIds)
    : new Map<string, Vendor>();

  const columns: Column<RecurringExpenseTemplate>[] = [
    {
      key: "description",
      header: "Expense",
      cell: (row) => (
        <span>
          {row.description || <span className="text-ink-400">No description</span>}
          {row.vendor ? <span className="block text-xs text-ink-500">{vendors.get(row.vendor)?.display_name ?? "Vendor"}</span> : null}
        </span>
      ),
    },
    { key: "frequency", header: "Repeats", cell: (row) => RECURRING_FREQUENCY_LABELS[row.frequency] ?? row.frequency },
    {
      key: "next_run_at",
      header: "Next run",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.next_run_at)}</span>,
    },
    {
      key: "end_date",
      header: "Ends",
      hideBelow: "md",
      cell: (row) => (row.end_date ? <span className="tabular">{formatDate(row.end_date)}</span> : <span className="text-ink-400">Never</span>),
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
    {
      key: "amount",
      header: "Amount",
      numeric: true,
      cell: (row) => (
        <span>
          <Money value={row.amount} currency={row.currency} strong />
          <span className="block text-2xs text-ink-500">before tax</span>
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Recurring expenses"
        description="Templates that create a draft expense on a schedule."
        actions={
          canManage ? (
            <LinkButton href="/purchases/recurring-expenses/new" variant="primary">
              New recurring expense
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
          caption="Recurring expense templates"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/purchases/recurring-expenses/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No recurring expenses match these filters" : "No recurring expenses yet"}
          emptyDescription="Set up a recurring expense for a cost paid on a schedule, like rent."
          {...(canManage ? { emptyAction: { label: "New recurring expense", href: "/purchases/recurring-expenses/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "next run date"} />}
        />
      </PageBody>
    </>
  );
}
