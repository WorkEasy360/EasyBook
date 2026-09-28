import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { SortNote } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { ForbiddenState } from "@/components/ui/states";
import { WarehouseDialogButton } from "@/features/inventory/warehouse-dialog";
import { serverApi, tryServer } from "@/lib/api/server";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { Warehouse } from "@/types/api/inventory";

export const metadata: Metadata = { title: "Warehouses" };

export default async function WarehousesPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return (
      <>
        <PageHeader title="Warehouses" />
        <ForbiddenState resource="warehouses" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_WAREHOUSES);
  const query = parseListQuery("/inventory/warehouses", "inventory/warehouses", params);
  const result = await tryServer(() =>
    serverApi.list<Warehouse>("inventory/warehouses", { query: query.apiParams }),
  );

  const columns: Column<Warehouse>[] = [
    { key: "code", header: "Code", cell: (row) => <span className="tabular font-medium">{row.code}</span> },
    {
      key: "name",
      header: "Warehouse",
      cell: (row) => (
        <span className="flex items-center gap-2">
          {row.name}
          {row.is_default ? (
            <Badge tone="brand" size="sm">
              Default
            </Badge>
          ) : null}
        </span>
      ),
    },
    {
      key: "address",
      header: "Address",
      hideBelow: "md",
      cell: (row) =>
        row.address ? (
          <span className="line-clamp-1 max-w-xs text-ink-600">{row.address}</span>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    {
      key: "is_active",
      header: "Status",
      cell: (row) => (
        <Badge tone={row.is_active ? "success" : "neutral"} marker={row.is_active} size="sm">
          {row.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
    {
      key: "stock",
      header: "",
      headerLabel: "Stock",
      numeric: true,
      cell: (row) => (
        <span className="flex items-center justify-end gap-3">
          <LinkButton href={`/inventory?warehouse=${row.id}`} variant="ghost" size="sm">
            Stock<span className="sr-only"> in {row.name}</span>
          </LinkButton>
          {canManage ? <WarehouseDialogButton warehouse={row} /> : null}
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Warehouses"
        description="Locations that hold stock. Warehouses cannot be deleted — deactivate one instead."
        actions={canManage ? <WarehouseDialogButton /> : null}
      />
      <PageBody>
        <DataTable
          caption="Warehouses"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          emptyTitle="No warehouses yet"
          emptyDescription="Stock has to live somewhere. Add a warehouse before receiving or adjusting stock."
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description="code" />}
        />
      </PageBody>
    </>
  );
}
