import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { InvoiceForm } from "@/features/sales/invoice-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Invoice } from "@/types/api/sales";

export const metadata: Metadata = { title: "Edit invoice" };

export default async function EditInvoicePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.CREATE_INVOICE)) {
    return (
      <>
        <PageHeader title="Edit invoice" />
        <ForbiddenState resource="editing invoices" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Invoice>(`sales/invoices/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit invoice" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (invoice_not_draft). A posted invoice is
  // corrected by voiding it or raising a credit note, never by editing.
  if (result.data.status !== "draft") redirect(`/sales/invoices/${id}`);

  return (
    <>
      <PageHeader
        title="Edit draft invoice"
        breadcrumbs={[
          { label: "Invoices", href: "/sales/invoices" },
          { label: "Draft invoice", href: `/sales/invoices/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <InvoiceForm invoice={result.data} />
      </PageBody>
    </>
  );
}
