import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { GOODS_RECEIPT_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { indexList } from "@/lib/api/lookups";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { GoodsReceipt, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Goods receipts" };

const RESOURCE = "purchases/goods-receipts";

export default async function GoodsReceiptsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_GOODS_RECEIPTS)) {
    return (
      <>
        <PageHeader title="Goods receipts" />
        <ForbiddenState resource="goods receipts" />
      </>
    );
  }

  const canCreate = roleHasPermission(session.role, PERMISSIONS.MANAGE_GOODS_RECEIPTS);
  const query = parseListQuery("/purchases/goods-receipts", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<GoodsReceipt>(RESOURCE, { query: query.apiParams }));

  const vendorFilter = query.filters["vendor"];
  const vendorIds = result.ok ? result.data.results.map((row) => row.vendor) : [];
  if (vendorFilter) vendorIds.push(vendorFilter);
  const vendors = roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)
    ? await indexList<Vendor>("purchases/vendors", vendorIds)
    : new Map<string, Vendor>();

  const columns: Column<GoodsReceipt>[] = [
    { key: "receipt_number", header: "Receipt", cell: (row) => <span className="tabular">{row.receipt_number}</span> },
    {
      key: "vendor",
      header: "Vendor",
      cell: (row) => vendors.get(row.vendor)?.display_name ?? <span className="text-ink-400">Vendor</span>,
    },
    {
      key: "receipt_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.receipt_date)}</span>,
    },
    {
      key: "vendor_document_number",
      header: "Delivery note",
      hideBelow: "md",
      cell: (row) => row.vendor_document_number || <span className="text-ink-400">—</span>,
    },
    {
      key: "lines",
      header: "Lines",
      numeric: true,
      hideBelow: "md",
      cell: (row) => row.lines.length,
    },
    { key: "status", header: "Status", cell: (row) => <StatusBadge status={row.status} map={GOODS_RECEIPT_STATUS} size="sm" /> },
  ];

  return (
    <>
      <PageHeader
        title="Goods receipts"
        description="Goods that have physically arrived. Receiving one adds the stock; the bill that follows posts the ledger entry."
        actions={
          canCreate ? (
            <LinkButton href="/purchases/goods-receipts/new" variant="primary">
              New goods receipt
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            { key: "status", label: "Status", value: query.filters["status"] ?? "", options: statusOptions(GOODS_RECEIPT_STATUS) },
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
          caption="Goods receipts"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/purchases/goods-receipts/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No goods receipts match these filters" : "No goods receipts yet"}
          emptyDescription="Record a goods receipt when a delivery arrives, usually from an approved purchase order."
          {...(canCreate ? { emptyAction: { label: "New goods receipt", href: "/purchases/goods-receipts/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
