import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { CreditNoteForm } from "@/features/sales/credit-note-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { CreditNote, Invoice } from "@/types/api/sales";

export const metadata: Metadata = { title: "Edit credit note" };

export default async function EditCreditNotePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.ISSUE_CREDIT_NOTE)) {
    return (
      <>
        <PageHeader title="Edit credit note" />
        <ForbiddenState resource="editing credit notes" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<CreditNote>(`sales/credit-notes/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit credit note" />
        <ErrorState title="Could not load the credit note" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (credit_note_not_draft). An issued credit note
  // is corrected by voiding it, which reverses its journal and restock.
  if (result.data.status !== "draft") redirect(`/sales/credit-notes/${id}`);

  const creditNote = result.data;
  const invoice =
    creditNote.source_invoice && roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? await tryServer(() => serverApi.get<Invoice>(`sales/invoices/${creditNote.source_invoice}`))
      : null;

  return (
    <>
      <PageHeader
        title="Edit draft credit note"
        breadcrumbs={[
          { label: "Credit notes", href: "/sales/credit-notes" },
          { label: "Draft credit note", href: `/sales/credit-notes/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <CreditNoteForm
          creditNote={creditNote}
          sourceInvoice={
            invoice?.ok
              ? { number: invoice.data.invoice_number || "draft", amountDue: invoice.data.amount_due, currency: invoice.data.currency }
              : null
          }
        />
      </PageBody>
    </>
  );
}
