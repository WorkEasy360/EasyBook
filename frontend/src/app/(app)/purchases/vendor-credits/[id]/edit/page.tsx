import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { VendorCreditForm } from "@/features/purchases/vendor-credit-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { VendorCredit } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Edit vendor credit" };

export default async function EditVendorCreditPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.ISSUE_VENDOR_CREDIT)) {
    return (
      <>
        <PageHeader title="Edit vendor credit" />
        <ForbiddenState resource="editing vendor credits" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<VendorCredit>(`purchases/vendor-credits/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit vendor credit" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (vendor_credit_not_draft). An issued credit is
  // reversed by voiding it.
  if (result.data.status !== "draft") redirect(`/purchases/vendor-credits/${id}`);

  return (
    <>
      <PageHeader
        title="Edit draft vendor credit"
        breadcrumbs={[
          { label: "Vendor credits", href: "/purchases/vendor-credits" },
          { label: "Draft vendor credit", href: `/purchases/vendor-credits/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <VendorCreditForm credit={result.data} />
      </PageBody>
    </>
  );
}
