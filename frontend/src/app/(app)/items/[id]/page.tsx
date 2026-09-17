import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody, DetailList, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Money, Quantity } from "@/components/ui/money";
import { DataTable, type Column } from "@/components/ui/data-table";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Item, UnitOfMeasure } from "@/types/api/items";
import type { InventoryPositionRow, InventorySummary } from "@/types/api/inventory";
import type { Account } from "@/types/api/accounting";

export const metadata: Metadata = { title: "Item" };

export default async function ItemDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_ITEMS)) {
    return (
      <>
        <PageHeader title="Item" />
        <ForbiddenState resource="items" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_ITEMS);
  const canViewInventory = roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY);

  const itemResult = await tryServer(() => serverApi.get<Item>(`items/${id}`));

  if (!itemResult.ok) {
    const error = itemResult.error;
    if (error instanceof ApiError && error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Item" />
        <ErrorState message={error.message} reference={referenceOf(error)} />
      </>
    );
  }

  const item = itemResult.data;
  const showsStock = item.item_type === "product" && item.track_inventory && canViewInventory;

  // Only fetched once we know this item can hold stock — a service has none.
  const [stock, units, accounts] = await Promise.all([
    showsStock
      ? tryServer(() =>
          // The inventory REPORT, not /inventory/stock-summary/: only the
          // report breaks the position down per warehouse with its value.
          serverApi.get<InventorySummary>("reports/inventory/summary", {
            query: { item: item.id },
          }),
        )
      : Promise.resolve({ ok: false as const, error: new Error("not stocked") }),
    tryServer(() => serverApi.list<UnitOfMeasure>("items/units", { query: { page_size: 200 } })),
    roleHasPermission(session.role, PERMISSIONS.VIEW_ACCOUNTING)
      ? tryServer(() =>
          serverApi.list<Account>("accounting/accounts", { query: { page_size: 200 } }),
        )
      : Promise.resolve({ ok: false as const, error: new Error("no permission") }),
  ]);

  const unit = units.ok ? units.data.results.find((row) => row.id === item.unit) : undefined;
  const unitName = unit?.name ?? item.unit;
  const unitSymbol = unit ? unit.symbol || unit.code : null;

  const accountName = (accountId: string | null) => {
    if (!accountId) return <span className="text-ink-400">Not set</span>;
    // Without accounting access the chart cannot be read; say an account is
    // set rather than printing its raw id.
    if (!accounts.ok) return "Configured";
    const account = accounts.data.results.find((row) => row.id === accountId);
    return account ? `${account.code} · ${account.name}` : "Configured";
  };

  const stockRows = stock.ok ? stock.data.rows : [];

  const stockColumns: Column<InventoryPositionRow>[] = [
    {
      key: "warehouse",
      header: "Warehouse",
      cell: (row) => row.warehouse_name,
    },
    {
      key: "quantity_on_hand",
      header: "On hand",
      numeric: true,
      cell: (row) => <Quantity value={row.quantity_on_hand} unit={unitSymbol} />,
    },
    {
      key: "average_cost",
      header: "Average cost",
      numeric: true,
      hideBelow: "sm",
      cell: (row) => <Money value={row.average_cost} />,
    },
    {
      key: "inventory_value",
      header: "Value",
      numeric: true,
      cell: (row) => <Money value={row.inventory_value} />,
    },
  ];

  return (
    <>
      <PageHeader
        title={item.name}
        breadcrumbs={[{ label: "Items", href: "/items" }, { label: item.name }]}
        meta={
          <>
            <Badge tone={item.item_type === "product" ? "info" : "neutral"}>
              {item.item_type === "product" ? "Product" : "Service"}
            </Badge>
            <Badge tone={item.is_active ? "success" : "neutral"} marker={item.is_active}>
              {item.is_active ? "Active" : "Inactive"}
            </Badge>
          </>
        }
        description={<span className="tabular">{item.sku}</span>}
        actions={
          canManage ? (
            <Link
              href={`/items/${item.id}/edit`}
              className="inline-flex h-9 items-center rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 transition-colors hover:bg-ink-50"
            >
              Edit
            </Link>
          ) : null
        }
      />

      <PageBody>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Unit", value: unitName },
                  { label: "Tax category", value: item.tax_category || "—" },
                  { label: "Sales price", value: <Money value={item.sales_price} /> },
                  { label: "Purchase price", value: <Money value={item.purchase_price} /> },
                  { label: "Sellable", value: item.is_sellable ? "Yes" : "No" },
                  { label: "Purchasable", value: item.is_purchasable ? "Yes" : "No" },
                  {
                    label: "Inventory",
                    value:
                      item.item_type === "service"
                        ? "Not applicable to services"
                        : item.track_inventory
                          ? `Tracked · reorder at ${item.reorder_level}`
                          : "Not tracked",
                  },
                  ...(item.description
                    ? [{ label: "Description", value: item.description, span: true }]
                    : []),
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Accounts" description="Where this item posts." />
            <CardBody>
              <DetailList
                columns={1}
                items={[
                  { label: "Sales", value: accountName(item.sales_account) },
                  { label: "Purchase", value: accountName(item.purchase_account) },
                  ...(item.track_inventory
                    ? [
                        { label: "Inventory", value: accountName(item.inventory_account) },
                        { label: "Cost of goods sold", value: accountName(item.cogs_account) },
                      ]
                    : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        {showsStock ? (
          <Section
            title="Stock on hand"
            description="Positions come from the movement ledger. There is no direct quantity edit — use a stock adjustment."
            actions={
              <Link
                href={`/inventory/movements?item_id=${item.id}`}
                className="text-xs font-medium text-brand-700 hover:underline"
              >
                Movement history
              </Link>
            }
          >
            <DataTable
              caption={`Stock on hand for ${item.name}`}
              columns={stockColumns}
              data={{ count: stockRows.length, next: null, previous: null, results: stockRows }}
              error={stock.ok ? null : (stock.error as ApiError)}
              getRowId={(row) => row.warehouse_id}
              emptyTitle="No stock recorded"
              emptyDescription="This item has no movements yet."
              page={1}
              pageSize={stockRows.length || 1}
              buildPageHref={() => `/items/${item.id}`}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
