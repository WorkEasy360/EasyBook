import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/ui/states";
import { ItemForm } from "@/features/items/item-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import type { UnitOfMeasure } from "@/types/api/items";

export const metadata: Metadata = { title: "New item" };

const CRUMBS = [{ label: "Items", href: "/items" }, { label: "New" }];

export default async function NewItemPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_ITEMS)) {
    return (
      <>
        <PageHeader title="New item" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating items" />
      </>
    );
  }

  const units = await tryServer(() =>
    serverApi.list<UnitOfMeasure>("items/units", { query: { page_size: 200 } }),
  );

  if (!units.ok) {
    return (
      <>
        <PageHeader title="New item" breadcrumbs={CRUMBS} />
        <ErrorState message={units.error.message} reference={referenceOf(units.error)} />
      </>
    );
  }

  const activeUnits = units.data.results.filter((unit) => unit.is_active);

  return (
    <>
      <PageHeader title="New item" breadcrumbs={CRUMBS} />
      <PageBody className="max-w-3xl">
        {activeUnits.length === 0 ? (
          // Every item needs a unit, and a new organization starts with none —
          // nothing seeds them. Sending the user to a form they cannot submit
          // would be a dead end.
          <EmptyState
            title="Add a unit of measure first"
            description="Every item is counted in a unit — pieces, hours, kilograms. Create one, then come back."
            action={{ label: "Manage units", href: "/items/units" }}
          />
        ) : (
          <ItemForm units={activeUnits} />
        )}
      </PageBody>
    </>
  );
}
