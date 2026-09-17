import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { GoodsReceiptForm } from "@/features/purchases/goods-receipt-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { GoodsReceipt } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Edit goods receipt" };

export default async function EditGoodsReceiptPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_GOODS_RECEIPTS)) {
    return (
      <>
        <PageHeader title="Edit goods receipt" />
        <ForbiddenState resource="editing goods receipts" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<GoodsReceipt>(`purchases/goods-receipts/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit goods receipt" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (goods_receipt_not_draft): once received, the
  // stock has moved and a return goes through a vendor credit instead.
  if (result.data.status !== "draft") redirect(`/purchases/goods-receipts/${id}`);

  const receipt = result.data;

  return (
    <>
      <PageHeader
        title={`Edit ${receipt.receipt_number}`}
        breadcrumbs={[
          { label: "Goods receipts", href: "/purchases/goods-receipts" },
          { label: receipt.receipt_number, href: `/purchases/goods-receipts/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-5xl">
        <GoodsReceiptForm receipt={receipt} />
      </PageBody>
    </>
  );
}
