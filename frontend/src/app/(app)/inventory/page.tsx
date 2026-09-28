import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody, Section } from "@/components/ui/detail";
import { DataTable, TableFooterRow, type Column } from "@/components/ui/data-table";
import { FilterBar } from "@/components/ui/filter-bar";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { LinkButton } from "@/components/ui/link-button";
import { Money, Quantity } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { ForbiddenState } from "@/components/ui/states";
import { Icons } from "@/components/ui/icons";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import { dateParamOf, paramOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type {
  InventoryPositionRow,
  InventorySummary,
  InventoryValuation,
  LowStockItemRow,
  Warehouse,
} from "@/types/api/inventory";

export const metadata: Metadata = { title: "Inventory" };

/**
 * Stock on hand and its value, per item and warehouse.
 *
 * Every figure here comes from the backend's inventory reports, which read the
 * movement ledger and value it at moving average cost. Nothing is summed or
 * valued in the browser — the total is the valuation report's own
 * `total_value`, not a sum of the rows shown (spec §8, §38).
 */
export default async function InventoryPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return (
      <>
        <PageHeader title="Inventory" />
        <ForbiddenState resource="inventory" />
      </>
    );
  }

  const warehouseId = paramOf(params, "warehouse");
  const asOf = dateParamOf(params, "as_of_date");
  const reportQuery = { warehouse: warehouseId, as_of_date: asOf };

  const [summary, valuation, lowStock, warehouses] = await Promise.all([
    tryServer(() => serverApi.get<InventorySummary>("reports/inventory/summary", { query: reportQuery })),
    tryServer(() => serverApi.get<InventoryValuation>("reports/inventory/valuation", { query: reportQuery })),
    // The low-stock report has no as-of date: it is always "now".
    tryServer(() =>
      serverApi.get<{ rows: LowStockItemRow[] }>("reports/inventory/low-stock", {
        query: { warehouse: warehouseId },
      }),
    ),
    tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200 } })),
  ]);

  const canAdjust = roleHasPermission(session.role, PERMISSIONS.ADJUST_INVENTORY);
  const canTransfer = roleHasPermission(session.role, PERMISSIONS.TRANSFER_INVENTORY);

  const rows = summary.ok ? summary.data.rows : [];
  const lowRows = lowStock.ok ? lowStock.data.rows : [];
  const warehouseList = warehouses.ok ? warehouses.data.results : [];
  const selectedWarehouse = warehouseList.find((row) => row.id === warehouseId);

  const csvHref = csvExportHref("inventory/summary", reportQuery);

  function filterHref(key: string, value: string | null): string {
    const next = new URLSearchParams();
    if (asOf) next.set("as_of_date", asOf);
    if (warehouseId) next.set("warehouse", warehouseId);
    if (value) next.set(key, value);
    else next.delete(key);
    const query = next.toString();
    return query ? `/inventory?${query}` : "/inventory";
  }

  const columns: Column<InventoryPositionRow>[] = [
    { key: "item_name", header: "Item", cell: (row) => row.item_name },
    {
      key: "item_sku",
      header: "SKU",
      hideBelow: "sm",
      cell: (row) => <span className="tabular text-ink-600">{row.item_sku}</span>,
    },
    { key: "warehouse_name", header: "Warehouse", hideBelow: "md", cell: (row) => row.warehouse_name },
    {
      key: "quantity_on_hand",
      header: "On hand",
      numeric: true,
      cell: (row) => <Quantity value={row.quantity_on_hand} />,
    },
    {
      key: "average_cost",
      header: "Avg. cost",
      numeric: true,
      hideBelow: "lg",
      cell: (row) => <Money value={row.average_cost} />,
    },
    {
      key: "inventory_value",
      header: "Value",
      numeric: true,
      cell: (row) => <Money value={row.inventory_value} />,
    },
  ];

  const lowColumns: Column<LowStockItemRow>[] = [
    { key: "item_name", header: "Item", cell: (row) => row.item_name },
    {
      key: "item_sku",
      header: "SKU",
      hideBelow: "sm",
      cell: (row) => <span className="tabular text-ink-600">{row.item_sku}</span>,
    },
    {
      key: "on_hand",
      header: "On hand",
      numeric: true,
      cell: (row) => <Quantity value={row.on_hand} className="text-danger-600" />,
    },
    {
      key: "reorder_level",
      header: "Reorder level",
      numeric: true,
      cell: (row) => <Quantity value={row.reorder_level} />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Inventory"
        description={
          asOf
            ? `Stock position as of ${formatDate(asOf)}.`
            : "Current stock position, valued at moving average cost."
        }
        actions={
          <>
            {csvHref ? (
              <LinkButton href={csvHref} download>
                <Icons.download className="size-3.5" aria-hidden="true" />
                Export CSV
              </LinkButton>
            ) : null}
            <LinkButton href="/inventory/movements">Movements</LinkButton>
            {canTransfer ? <LinkButton href="/inventory/transfers/new">Transfer stock</LinkButton> : null}
            {canAdjust ? (
              <LinkButton href="/inventory/adjustments/new" variant="primary">
                New adjustment
              </LinkButton>
            ) : null}
          </>
        }
      />

      <PageBody>
        <StatGrid columns={3}>
          <StatCard
            label={selectedWarehouse ? `Value · ${selectedWarehouse.name}` : "Inventory value"}
            value={valuation.ok ? <Money value={valuation.data.total_value} /> : "—"}
            hint={valuation.ok ? "From the valuation report" : "Valuation unavailable"}
          />
          <StatCard label="Stock positions" value={rows.length} hint="Item × warehouse with stock" />
          <StatCard
            label="Below reorder level"
            value={lowStock.ok ? lowRows.length : "—"}
            tone={lowRows.length > 0 ? "warning" : "default"}
            {...(lowRows.length > 0 ? { href: "#low-stock" } : {})}
          />
        </StatGrid>

        <div className="flex flex-col gap-3">
          {warehouseList.length > 1 ? (
            <FilterBar
              groups={[
                {
                  key: "warehouse",
                  label: "Warehouse",
                  value: warehouseId ?? "",
                  allLabel: "All warehouses",
                  options: warehouseList.map((row) => ({ value: row.id, label: row.name })),
                },
              ]}
              buildFilterHref={filterHref}
              clearHref={asOf ? `/inventory?as_of_date=${asOf}` : "/inventory"}
              activeFilterCount={warehouseId ? 1 : 0}
            />
          ) : null}

          <DateFilterForm
            action="/inventory"
            fields={[{ name: "as_of_date", label: "As of", value: asOf }]}
            preserve={{ warehouse: warehouseId }}
            {...(asOf ? { clearHref: warehouseId ? `/inventory?warehouse=${warehouseId}` : "/inventory" } : {})}
          />
        </div>

        <DataTable
          caption="Stock on hand"
          columns={columns}
          data={summary.ok ? wholeList(rows) : undefined}
          error={summary.ok ? null : (summary.error as ApiError)}
          getRowId={(row) => `${row.item_id}-${row.warehouse_id}`}
          getRowHref={(row) => `/items/${row.item_id}`}
          emptyTitle="No stock on hand"
          emptyDescription="Stock appears here once a goods receipt, bill or stock adjustment is posted."
          {...(canAdjust
            ? { emptyAction: { label: "New adjustment", href: "/inventory/adjustments/new" } }
            : {})}
          page={1}
          pageSize={Math.max(rows.length, 1)}
          buildPageHref={() => "/inventory"}
          footer={
            valuation.ok && rows.length > 0 ? (
              <TableFooterRow
                label="Total value"
                columnCount={columns.length}
                values={[{ key: "value", node: <Money value={valuation.data.total_value} strong /> }]}
              />
            ) : undefined
          }
        />

        <Section
          title="Below reorder level"
          description={
            selectedWarehouse
              ? `Items whose stock in ${selectedWarehouse.name} is at or below their reorder level.`
              : "Items whose total stock across warehouses is at or below their reorder level."
          }
          className="scroll-mt-4"
        >
          <div id="low-stock">
            <DataTable
              caption="Items below reorder level"
              columns={lowColumns}
              data={lowStock.ok ? wholeList(lowRows) : undefined}
              error={lowStock.ok ? null : (lowStock.error as ApiError)}
              getRowId={(row) => row.item_id}
              getRowHref={(row) => `/items/${row.item_id}`}
              emptyTitle="Nothing below reorder level"
              emptyDescription="Set a reorder level on an item to be warned here."
              page={1}
              pageSize={Math.max(lowRows.length, 1)}
              buildPageHref={() => "/inventory"}
            />
          </div>
          {lowRows.length > 0 && roleHasPermission(session.role, PERMISSIONS.MANAGE_PURCHASE_ORDERS) ? (
            <p className="text-xs text-ink-500">
              Reorder through a{" "}
              <Link href="/purchases/orders/new" className="font-medium text-brand-700 hover:underline">
                new purchase order
              </Link>
              .
            </p>
          ) : null}
        </Section>
      </PageBody>
    </>
  );
}
