import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { SortNote } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { ForbiddenState } from "@/components/ui/states";
import { UnitDialogButton } from "@/features/items/unit-dialog";
import { serverApi, tryServer } from "@/lib/api/server";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { UnitOfMeasure } from "@/types/api/items";

export const metadata: Metadata = { title: "Units of measure" };

const CRUMBS = [{ label: "Items", href: "/items" }, { label: "Units" }];

export default async function UnitsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_ITEMS)) {
    return (
      <>
        <PageHeader title="Units of measure" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="units of measure" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_ITEMS);
  const query = parseListQuery("/items/units", "items/units", params);
  const result = await tryServer(() =>
    serverApi.list<UnitOfMeasure>("items/units", { query: query.apiParams }),
  );

  const columns: Column<UnitOfMeasure>[] = [
    { key: "code", header: "Code", cell: (unit) => <span className="tabular font-medium">{unit.code}</span> },
    { key: "name", header: "Name", cell: (unit) => unit.name },
    {
      key: "symbol",
      header: "Symbol",
      cell: (unit) => unit.symbol || <span className="text-ink-400">—</span>,
    },
    {
      key: "is_system",
      header: "Source",
      hideBelow: "sm",
      cell: (unit) => (
        <Badge tone={unit.is_system ? "brand" : "neutral"} size="sm">
          {unit.is_system ? "System" : "Custom"}
        </Badge>
      ),
    },
    {
      key: "is_active",
      header: "Status",
      cell: (unit) => (
        <Badge tone={unit.is_active ? "success" : "neutral"} marker={unit.is_active} size="sm">
          {unit.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
  ];

  if (canManage) {
    columns.push({
      key: "actions",
      header: "",
      headerLabel: "Actions",
      numeric: true,
      cell: (unit) => <UnitDialogButton unit={unit} />,
    });
  }

  return (
    <>
      <PageHeader
        title="Units of measure"
        description="How items are counted on documents and in stock. Units cannot be deleted — deactivate one instead."
        breadcrumbs={CRUMBS}
        actions={canManage ? <UnitDialogButton /> : null}
      />
      <PageBody>
        <DataTable
          caption="Units of measure"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(unit) => unit.id}
          emptyTitle="No units yet"
          emptyDescription={
            canManage
              ? "Add the units your items are counted in before creating items."
              : "An administrator needs to add units before items can be created."
          }
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description="code" />}
        />
      </PageBody>
    </>
  );
}
