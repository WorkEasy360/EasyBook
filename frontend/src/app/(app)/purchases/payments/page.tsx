import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PAYMENT_METHOD_LABELS } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { indexList } from "@/lib/api/lookups";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Vendor, VendorPayment } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Payments made" };

const RESOURCE = "purchases/payments";

export default async function VendorPaymentsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_VENDOR_PAYMENTS)) {
    return (
      <>
        <PageHeader title="Payments made" />
        <ForbiddenState resource="vendor payments" />
      </>
    );
  }

  const canRecord = roleHasPermission(session.role, PERMISSIONS.RECORD_VENDOR_PAYMENT);
  const query = parseListQuery("/purchases/payments", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<VendorPayment>(RESOURCE, { query: query.apiParams }));

  const vendorFilter = query.filters["vendor"];
  const vendorIds = result.ok ? result.data.results.map((row) => row.vendor) : [];
  if (vendorFilter) vendorIds.push(vendorFilter);
  const vendors = roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)
    ? await indexList<Vendor>("purchases/vendors", vendorIds)
    : new Map<string, Vendor>();

  const columns: Column<VendorPayment>[] = [
    { key: "payment_number", header: "Payment", cell: (row) => <span className="tabular">{row.payment_number}</span> },
    {
      key: "vendor",
      header: "Vendor",
      cell: (row) => vendors.get(row.vendor)?.display_name ?? <span className="text-ink-400">Vendor</span>,
    },
    {
      key: "payment_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.payment_date)}</span>,
    },
    {
      key: "payment_method",
      header: "Method",
      hideBelow: "md",
      cell: (row) => PAYMENT_METHOD_LABELS[row.payment_method] ?? row.payment_method,
    },
    {
      key: "reference",
      header: "Reference",
      hideBelow: "lg",
      cell: (row) => row.reference || <span className="text-ink-400">—</span>,
    },
    {
      key: "bills",
      header: "Bills",
      numeric: true,
      hideBelow: "md",
      cell: (row) => row.allocations.filter((allocation) => allocation.bill !== null).length,
    },
    { key: "amount", header: "Amount", numeric: true, cell: (row) => <Money value={row.amount} currency={row.currency} strong /> },
  ];

  return (
    <>
      <PageHeader
        title="Payments made"
        description="Money paid to vendors. A payment is posted when it is recorded and cannot be edited."
        actions={
          canRecord ? (
            <LinkButton href="/purchases/payments/new" variant="primary">
              Record payment
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        {vendorFilter ? (
          <FilterBar
            groups={[
              {
                key: "vendor",
                label: "Vendor",
                value: vendorFilter,
                options: [{ value: vendorFilter, label: vendors.get(vendorFilter)?.display_name ?? "Selected vendor" }],
              },
            ]}
            buildFilterHref={query.buildFilterHref}
            clearHref={query.clearHref}
            activeFilterCount={query.activeFilterCount}
          />
        ) : null}
        <DataTable
          caption="Vendor payments"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/purchases/payments/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No payments to this vendor" : "No payments recorded yet"}
          emptyDescription="Record a payment when you pay a vendor's bills."
          {...(canRecord ? { emptyAction: { label: "Record payment", href: "/purchases/payments/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
