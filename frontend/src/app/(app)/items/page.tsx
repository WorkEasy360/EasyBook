import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { Money } from "@/components/ui/money";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { Item } from "@/types/api/items";

export const metadata: Metadata = { title: "Items" };

export default async function ItemsPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_ITEMS)) {
    return (
      <>
        <PageHeader title="Items" />
        <ForbiddenState resource="items" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_ITEMS);
  const query = parseListQuery("/items", "items", params);
  const result = await tryServer(() => serverApi.list<Item>("items", { query: query.apiParams }));

  const columns: Column<Item>[] = [
    { key: "name", header: "Item", cell: (item) => item.name },
    {
      key: "sku",
      header: "SKU",
      hideBelow: "sm",
      cell: (item) => <span className="tabular text-ink-600">{item.sku}</span>,
    },
    {
      key: "item_type",
      header: "Type",
      cell: (item) => (
        <Badge tone={item.item_type === "product" ? "info" : "neutral"} size="sm">
          {item.item_type === "product" ? "Product" : "Service"}
        </Badge>
      ),
    },
    {
      key: "sales_price",
      header: "Sales price",
      numeric: true,
      cell: (item) => <Money value={item.sales_price} />,
    },
    {
      key: "purchase_price",
      header: "Purchase price",
      numeric: true,
      hideBelow: "md",
      cell: (item) => <Money value={item.purchase_price} />,
    },
    {
      key: "track_inventory",
      header: "Stock",
      hideBelow: "md",
      cell: (item) =>
        /*
         * A service has no stock, and showing a reorder level against one is a
         * category error (spec §37). The dash is the honest answer.
         */
        item.track_inventory ? (
          <span className="tabular text-ink-600">Reorder at {item.reorder_level}</span>
        ) : (
          <span className="text-ink-400">Not tracked</span>
        ),
    },
    {
      key: "is_active",
      header: "Status",
      cell: (item) => (
        <Badge tone={item.is_active ? "success" : "neutral"} marker={item.is_active} size="sm">
          {item.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Items"
        description="Products and services you buy and sell."
        actions={
          <>
            <Link
              href="/items/units"
              className="inline-flex h-9 items-center rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 transition-colors hover:bg-ink-50"
            >
              Units
            </Link>
            {canManage ? (
              <Link
                href="/items/new"
                className="inline-flex h-9 items-center rounded-md border border-brand-700 bg-brand-700 px-3.5 text-sm font-medium text-white transition-colors hover:bg-brand-800"
              >
                New item
              </Link>
            ) : null}
          </>
        }
      />

      <PageBody>
        <FilterBar
          groups={[
            {
              key: "item_type",
              label: "Type",
              value: query.filters["item_type"] ?? "",
              options: [
                { value: "product", label: "Products" },
                { value: "service", label: "Services" },
              ],
            },
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
          caption="Items"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(item) => item.id}
          getRowHref={(item) => `/items/${item.id}`}
          emptyTitle="No items yet"
          emptyDescription="Add the products and services you trade in."
          {...(canManage ? { emptyAction: { label: "New item", href: "/items/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description="name" />}
        />
      </PageBody>
    </>
  );
}
