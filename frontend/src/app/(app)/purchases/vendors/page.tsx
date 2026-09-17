import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { ForbiddenState } from "@/components/ui/states";
import { paymentTermsLabel } from "@/features/purchases/labels";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Vendors" };

const RESOURCE = "purchases/vendors";

export default async function VendorsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_VENDORS)) {
    return (
      <>
        <PageHeader title="Vendors" />
        <ForbiddenState resource="vendors" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_VENDORS);
  const query = parseListQuery("/purchases/vendors", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<Vendor>(RESOURCE, { query: query.apiParams }));

  const columns: Column<Vendor>[] = [
    { key: "display_name", header: "Vendor", cell: (vendor) => vendor.display_name },
    {
      key: "vendor_code",
      header: "Code",
      hideBelow: "sm",
      cell: (vendor) => <span className="tabular text-ink-600">{vendor.vendor_code}</span>,
    },
    {
      key: "email",
      header: "Email",
      hideBelow: "md",
      cell: (vendor) => vendor.email || <span className="text-ink-400">—</span>,
    },
    {
      key: "gstin",
      header: "GSTIN",
      hideBelow: "lg",
      cell: (vendor) =>
        vendor.gstin ? <span className="tabular text-ink-600">{vendor.gstin}</span> : <span className="text-ink-400">—</span>,
    },
    {
      key: "payment_terms_days",
      header: "Terms",
      hideBelow: "md",
      cell: (vendor) => <span className="text-ink-600">{paymentTermsLabel(vendor.payment_terms_days)}</span>,
    },
    {
      key: "is_active",
      header: "Status",
      cell: (vendor) => (
        <Badge tone={vendor.is_active ? "success" : "neutral"} marker={vendor.is_active}>
          {vendor.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Vendors"
        description="Suppliers you order from, receive goods from and pay."
        actions={
          canManage ? (
            <LinkButton href="/purchases/vendors/new" variant="primary">
              New vendor
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
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Vendors"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(vendor) => vendor.id}
          getRowHref={(vendor) => `/purchases/vendors/${vendor.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No vendors match this filter" : "No vendors yet"}
          emptyDescription="Add a vendor to raise purchase orders and record bills."
          {...(canManage ? { emptyAction: { label: "New vendor", href: "/purchases/vendors/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "name"} />}
        />
      </PageBody>
    </>
  );
}
