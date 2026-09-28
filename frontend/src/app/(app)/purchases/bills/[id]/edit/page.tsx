import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { BillForm } from "@/features/purchases/bill-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Bill } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Edit bill" };

export default async function EditBillPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.CREATE_BILL)) {
    return (
      <>
        <PageHeader title="Edit bill" />
        <ForbiddenState resource="editing bills" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Bill>(`purchases/bills/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit bill" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (bill_not_draft). A posted bill is corrected by
  // voiding it or raising a vendor credit, never by editing.
  if (result.data.status !== "draft") redirect(`/purchases/bills/${id}`);

  return (
    <>
      <PageHeader
        title="Edit draft bill"
        breadcrumbs={[
          { label: "Bills", href: "/purchases/bills" },
          { label: "Draft bill", href: `/purchases/bills/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <BillForm bill={result.data} />
      </PageBody>
    </>
  );
}
