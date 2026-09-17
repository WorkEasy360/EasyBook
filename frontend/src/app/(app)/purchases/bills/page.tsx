import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { BILL_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { OverdueHint } from "@/features/documents/overdue-hint";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { indexList } from "@/lib/api/lookups";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate, todayInZone } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Bill, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Bills" };

const RESOURCE = "purchases/bills";

/** "overdue" is in BillStatus but never stored (see selectors.get_overdue_bills), so it is not offered as a filter. */
const FILTERABLE_STATUS = Object.fromEntries(Object.entries(BILL_STATUS).filter(([value]) => value !== "overdue"));

export default async function BillsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_BILLS)) {
    return (
      <>
        <PageHeader title="Bills" />
        <ForbiddenState resource="bills" />
      </>
    );
  }

  const canCreate = roleHasPermission(session.role, PERMISSIONS.CREATE_BILL);
  const query = parseListQuery("/purchases/bills", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<Bill>(RESOURCE, { query: query.apiParams }));

  const vendorFilter = query.filters["vendor"];
  const vendorIds = result.ok ? result.data.results.map((row) => row.vendor) : [];
  if (vendorFilter) vendorIds.push(vendorFilter);
  const vendors = roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)
    ? await indexList<Vendor>("purchases/vendors", vendorIds)
    : new Map<string, Vendor>();

  const today = todayInZone(session.timeZone);

  const columns: Column<Bill>[] = [
    {
      key: "bill_number",
      header: "Bill",
      cell: (row) => (row.bill_number ? <span className="tabular">{row.bill_number}</span> : <span className="italic">Draft</span>),
    },
    {
      key: "vendor",
      header: "Vendor",
      cell: (row) => (
        <span>
          {vendors.get(row.vendor)?.display_name ?? <span className="text-ink-400">Vendor</span>}
          {row.vendor_bill_number ? <span className="block text-xs text-ink-500 tabular">{row.vendor_bill_number}</span> : null}
        </span>
      ),
    },
    {
      key: "bill_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.bill_date)}</span>,
    },
    {
      key: "due_date",
      header: "Due",
      hideBelow: "md",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">
          {formatDate(row.due_date)}
          <OverdueHint invoice={row} today={today} />
        </span>
      ),
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={BILL_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, hideBelow: "sm", cell: (row) => <Money value={row.total} currency={row.currency} /> },
    {
      key: "amount_due",
      header: "Balance due",
      numeric: true,
      cell: (row) =>
        row.status === "draft" || row.status === "void" ? (
          <span className="text-ink-400">—</span>
        ) : (
          <Money value={row.amount_due} currency={row.currency} strong />
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Bills"
        description="Drafts can be edited freely. Posting a bill records the payable in the ledger and fixes its number."
        actions={
          canCreate ? (
            <LinkButton href="/purchases/bills/new" variant="primary">
              New bill
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            { key: "status", label: "Status", value: query.filters["status"] ?? "", options: statusOptions(FILTERABLE_STATUS) },
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
          caption="Bills"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/purchases/bills/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No bills match these filters" : "No bills yet"}
          emptyDescription="Record a bill when a vendor's invoice arrives."
          {...(canCreate ? { emptyAction: { label: "New bill", href: "/purchases/bills/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
