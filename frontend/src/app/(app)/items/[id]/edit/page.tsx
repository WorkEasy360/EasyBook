import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { ItemForm } from "@/features/items/item-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Item, UnitOfMeasure } from "@/types/api/items";

export const metadata: Metadata = { title: "Edit item" };

export default async function EditItemPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_ITEMS)) {
    return (
      <>
        <PageHeader title="Edit item" />
        <ForbiddenState resource="editing items" />
      </>
    );
  }

  const [itemResult, unitsResult] = await Promise.all([
    tryServer(() => serverApi.get<Item>(`items/${id}`)),
    tryServer(() => serverApi.list<UnitOfMeasure>("items/units", { query: { page_size: 200 } })),
  ]);

  if (!itemResult.ok) {
    const error = itemResult.error;
    if (error instanceof ApiError && error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit item" />
        <ErrorState message={error.message} reference={referenceOf(error)} />
      </>
    );
  }

  const item = itemResult.data;

  if (!unitsResult.ok) {
    return (
      <>
        <PageHeader title={`Edit ${item.name}`} />
        <ErrorState message={unitsResult.error.message} reference={referenceOf(unitsResult.error)} />
      </>
    );
  }

  // Keep the item's current unit selectable even if it has since been
  // deactivated, or the form would silently change it on save.
  const units = unitsResult.data.results.filter((unit) => unit.is_active || unit.id === item.unit);

  return (
    <>
      <PageHeader
        title={`Edit ${item.name}`}
        breadcrumbs={[
          { label: "Items", href: "/items" },
          { label: item.name, href: `/items/${item.id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-3xl">
        <ItemForm item={item} units={units} />
      </PageBody>
    </>
  );
}
