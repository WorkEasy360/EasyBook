import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { DELIVERY_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { customerFilterGroups } from "@/features/sales/list-helpers";
import { serverApi, tryServer } from "@/lib/api/server";
import { indexList } from "@/lib/api/lookups";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Warehouse } from "@/types/api/inventory";
import type { Customer, DeliveryChallan } from "@/types/api/sales";

export const metadata: Metadata = { title: "Delivery challans" };

const RESOURCE = "sales/deliveries";

export default async function DeliveriesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_DELIVERIES)) {
    return (
      <>
        <PageHeader title="Delivery challans" />
        <ForbiddenState resource="delivery challans" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_DELIVERIES);
  const query = parseListQuery("/sales/deliveries", RESOURCE, params);
  const result = await tryServer(() => serverApi.list<DeliveryChallan>(RESOURCE, { query: query.apiParams }));

  const rows = result.ok ? result.data.results : [];
  const customerIds = rows.map((row) => row.customer);
  if (query.filters["customer"]) customerIds.push(query.filters["customer"]);
  const [customers, warehouses] = await Promise.all([
    roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)
      ? indexList<Customer>("sales/customers", customerIds)
      : Promise.resolve(new Map<string, Customer>()),
    roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)
      ? indexList<Warehouse>("inventory/warehouses", rows.map((row) => row.warehouse))
      : Promise.resolve(new Map<string, Warehouse>()),
  ]);

  const columns: Column<DeliveryChallan>[] = [
    { key: "challan_number", header: "Challan", cell: (row) => <span className="tabular">{row.challan_number}</span> },
    {
      key: "customer",
      header: "Customer",
      cell: (row) => customers.get(row.customer)?.display_name ?? <span className="text-ink-400">Customer</span>,
    },
    {
      key: "challan_date",
      header: "Date",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.challan_date)}</span>,
    },
    {
      key: "warehouse",
      header: "Warehouse",
      hideBelow: "md",
      cell: (row) => warehouses.get(row.warehouse)?.name ?? <span className="text-ink-400">Warehouse</span>,
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={DELIVERY_STATUS} size="sm" />,
    },
    { key: "lines", header: "Lines", numeric: true, hideBelow: "sm", cell: (row) => row.lines.length },
  ];

  return (
    <>
      <PageHeader
        title="Delivery challans"
        description="Stock leaves the warehouse when a challan is dispatched. Bill a dispatched challan to invoice it without issuing the stock twice."
        actions={
          canManage ? (
            <LinkButton href="/sales/deliveries/new" variant="primary">
              New delivery challan
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "status",
              label: "Status",
              value: query.filters["status"] ?? "",
              options: statusOptions(DELIVERY_STATUS),
            },
            ...customerFilterGroups(query, customers),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />
        <DataTable
          caption="Delivery challans"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/sales/deliveries/${row.id}`}
          emptyTitle={query.activeFilterCount > 0 ? "No challans match these filters" : "No delivery challans yet"}
          emptyDescription="Raise a challan from a confirmed sales order, or on its own."
          {...(canManage ? { emptyAction: { label: "New delivery challan", href: "/sales/deliveries/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor(RESOURCE).defaultOrder ?? "newest first"} />}
        />
      </PageBody>
    </>
  );
}
