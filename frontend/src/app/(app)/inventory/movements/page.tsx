import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { Money, Quantity } from "@/components/ui/money";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatInstantAsDate } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import type { Item } from "@/types/api/items";
import {
  MOVEMENT_SOURCE_ROUTES,
  MOVEMENT_TYPE_LABELS,
  type MovementType,
  type StockMovement,
  type Warehouse,
} from "@/types/api/inventory";

export const metadata: Metadata = { title: "Stock movements" };

const INBOUND: ReadonlySet<MovementType> = new Set(["opening", "receipt", "adjustment_in", "transfer_in"]);

/**
 * The stock ledger: every movement, append-only.
 *
 * There is no edit or delete on a movement anywhere in the API, and none is
 * offered here — a wrong quantity is corrected by a new adjustment, which
 * leaves both entries visible (root CLAUDE.md rule 12).
 */
export default async function MovementsPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return (
      <>
        <PageHeader title="Stock movements" />
        <ForbiddenState resource="stock movements" />
      </>
    );
  }

  const query = parseListQuery("/inventory/movements", "inventory/movements", params);

  const [movements, warehouses, items] = await Promise.all([
    tryServer(() => serverApi.list<StockMovement>("inventory/movements", { query: query.apiParams })),
    tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200 } })),
    roleHasPermission(session.role, PERMISSIONS.VIEW_ITEMS)
      ? tryServer(() => serverApi.list<Item>("items", { query: { page_size: 200 } }))
      : Promise.resolve({ ok: false as const, error: new Error("no permission") }),
  ]);

  // The movement serializer returns ids only. Names are resolved from one
  // page of each list; anything beyond it falls back to a short id rather
  // than an extra request per row.
  const warehouseList = warehouses.ok ? warehouses.data.results : [];
  const warehouseName = new Map(warehouseList.map((row) => [row.id, row.name]));
  const itemName = new Map((items.ok ? items.data.results : []).map((row) => [row.id, row]));

  const filteredItem = query.filters["item_id"] ? itemName.get(query.filters["item_id"]) : undefined;

  const columns: Column<StockMovement>[] = [
    {
      key: "movement_date",
      header: "Date",
      cell: (row) => (
        <span className="tabular whitespace-nowrap">
          {formatInstantAsDate(row.movement_date, { timeZone: session.timeZone })}
        </span>
      ),
    },
    {
      key: "item",
      header: "Item",
      cell: (row) => {
        const item = itemName.get(row.item);
        return (
          <Link href={`/items/${row.item}`} className="text-brand-700 hover:underline">
            {item ? item.name : `Item ${row.item.slice(0, 8)}`}
          </Link>
        );
      },
    },
    {
      key: "warehouse",
      header: "Warehouse",
      hideBelow: "md",
      cell: (row) => warehouseName.get(row.warehouse) ?? `Warehouse ${row.warehouse.slice(0, 8)}`,
    },
    {
      key: "movement_type",
      header: "Type",
      cell: (row) => (
        <Badge tone={INBOUND.has(row.movement_type) ? "success" : "warning"} size="sm">
          {MOVEMENT_TYPE_LABELS[row.movement_type] ?? row.movement_type}
        </Badge>
      ),
    },
    {
      key: "quantity",
      header: "Quantity",
      numeric: true,
      // The ledger stores a positive quantity and a direction; the sign here
      // is presentation of that direction, not arithmetic.
      cell: (row) => (
        <span className={INBOUND.has(row.movement_type) ? "text-success-700" : "text-danger-600"}>
          <span aria-hidden="true">{INBOUND.has(row.movement_type) ? "+" : "−"}</span>
          <span className="sr-only">{INBOUND.has(row.movement_type) ? "in " : "out "}</span>
          <Quantity value={row.quantity} />
        </span>
      ),
    },
    {
      key: "unit_cost",
      header: "Unit cost",
      numeric: true,
      hideBelow: "lg",
      cell: (row) => <Money value={row.unit_cost} />,
    },
    {
      key: "source",
      header: "Source",
      hideBelow: "sm",
      cell: (row) => {
        const route = MOVEMENT_SOURCE_ROUTES[row.source_type];
        if (route && row.source_id) {
          return (
            <Link href={appHref(route.href(row.source_id))} className="text-brand-700 hover:underline">
              {route.label}
            </Link>
          );
        }
        return <span className="text-ink-600">{row.source_type || "—"}</span>;
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="Stock movements"
        breadcrumbs={[{ label: "Inventory", href: "/inventory" }, { label: "Movements" }]}
        description="Every change to stock, in the order it happened. Movements are never edited — corrections are new adjustments."
      />
      <PageBody>
        <FilterBar
          groups={[
            {
              key: "movement_type",
              label: "Type",
              value: query.filters["movement_type"] ?? "",
              options: Object.entries(MOVEMENT_TYPE_LABELS).map(([value, label]) => ({ value, label })),
            },
            ...(warehouseList.length > 1
              ? [
                  {
                    key: "warehouse_id",
                    label: "Warehouse",
                    value: query.filters["warehouse_id"] ?? "",
                    options: warehouseList.map((row) => ({ value: row.id, label: row.name })),
                  },
                ]
              : []),
            ...(query.filters["item_id"]
              ? [
                  {
                    key: "item_id",
                    label: "Item",
                    value: query.filters["item_id"],
                    options: [
                      {
                        value: query.filters["item_id"],
                        label: filteredItem ? filteredItem.name : "Selected item",
                      },
                    ],
                  },
                ]
              : []),
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />

        <DataTable
          caption="Stock movements"
          columns={columns}
          data={movements.ok ? movements.data : undefined}
          error={movements.ok ? null : (movements.error as ApiError)}
          getRowId={(row) => row.id}
          emptyTitle={query.activeFilterCount > 0 ? "No movements match these filters" : "No stock movements yet"}
          emptyDescription="Movements are written when receipts, deliveries, bills and adjustments are posted."
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description="movement date, oldest first" />}
        />
      </PageBody>
    </>
  );
}
