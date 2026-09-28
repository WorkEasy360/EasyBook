import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { AdjustmentForm } from "@/features/inventory/adjustment-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { StockAdjustment } from "@/types/api/inventory";

export const metadata: Metadata = { title: "Edit stock adjustment" };

export default async function EditAdjustmentPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.ADJUST_INVENTORY)) {
    return (
      <>
        <PageHeader title="Edit stock adjustment" />
        <ForbiddenState resource="adjusting stock" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<StockAdjustment>(`inventory/adjustments/${id}`));

  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit stock adjustment" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // A posted adjustment is part of the stock ledger. The API refuses edits
  // (adjustment_not_draft); sending the user to the record is kinder than
  // showing a form that can only fail.
  if (result.data.status !== "draft") redirect(`/inventory/adjustments/${id}`);

  return (
    <>
      <PageHeader
        title="Edit draft adjustment"
        breadcrumbs={[
          { label: "Stock adjustments", href: "/inventory/adjustments" },
          { label: "Draft", href: `/inventory/adjustments/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-5xl">
        <AdjustmentForm adjustment={result.data} />
      </PageBody>
    </>
  );
}
