import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import {
  StatusBadge,
  VENDOR_CREDIT_REASON_LABELS,
  VENDOR_CREDIT_STATUS,
  statusOptions,
} from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { indexList } from "@/lib/api/lookups";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Vendor, VendorCredit } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Vendor credits" };

const RESOURCE = "purchases/vendor-credits";

export default async function VendorCreditsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_VENDOR_CREDITS)) {
    return (
      <>
        <PageHeader title="Vendor credits" />
        <ForbiddenState resource="vendor credits" />
      </>
    );
  }

  const canCreate = roleHasPermission(session.role, PERMISSIONS.ISSUE_VENDOR_CREDIT);
  const query = parseListQuery("/purchases/vendor-credits", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<VendorCredit>(RESOURCE, { query: query.apiParams }));

  const vendorFilter = query.filters["vendor"];
  const vendorIds = result.ok ? result.data.results.map((row) => row.vendor) : [];
  if (vendorFilter) vendorIds.push(vendorFilter);
  const vendors = roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)
    ? await indexList<Vendor>("purchases/vendors", vendorIds)
    : new Map<string, Vendor>();

  const columns: Column<VendorCredit>[] = [
    {
      key: "credit_number",
      header: "Credit",
      cell: (row) => (row.credit_number ? <span className="tabular">{row.credit_number}</span> : <span className="italic">Draft</span>),
    },
    {
      key: "vendor",
      header: "Vendor",
      cell: (row) => vendors.get(row.vendor)?.display_name ?? <span className="text-ink-400">Vendor</span>,
    },
    {
      key: "credit_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.credit_date)}</span>,
    },
    {
      key: "reason",
      header: "Reason",
      hideBelow: "md",
      cell: (row) => VENDOR_CREDIT_REASON_LABELS[row.reason] ?? row.reason,
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={VENDOR_CREDIT_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, cell: (row) => <Money value={row.total} currency={row.currency} strong /> },
  ];

  return (
    <>
      <PageHeader
        title="Vendor credits"
        description="Credits from vendors for returns, pricing errors and discounts. Issuing one posts it and reduces what you owe."
        actions={
          canCreate ? (
            <LinkButton href="/purchases/vendor-credits/new" variant="primary">
              New vendor credit
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            { key: "status", label: "Status", value: query.filters["status"] ?? "", options: statusOptions(VENDOR_CREDIT_STATUS) },
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
          caption="Vendor credits"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/purchases/vendor-credits/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No vendor credits match these filters" : "No vendor credits yet"}
          emptyDescription="Raise a vendor credit from a bill when goods go back or a price is corrected."
          {...(canCreate ? { emptyAction: { label: "New vendor credit", href: "/purchases/vendor-credits/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
