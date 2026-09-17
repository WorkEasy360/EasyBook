import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { RecurringInvoiceForm } from "@/features/sales/recurring-invoice-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { RecurringInvoiceTemplate } from "@/types/api/sales";

export const metadata: Metadata = { title: "Edit recurring invoice" };

/**
 * No draft-only redirect here: a template has no draft state, and
 * RecurringInvoiceTemplateDetailView.update accepts its editable fields at
 * any time, active or not. Changes apply to invoices generated from now on;
 * drafts already generated keep what they were created with.
 */
export default async function EditRecurringInvoicePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_RECURRING_INVOICES)) {
    return (
      <>
        <PageHeader title="Edit recurring invoice" />
        <ForbiddenState resource="editing recurring invoices" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<RecurringInvoiceTemplate>(`sales/recurring-invoices/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit recurring invoice" />
        <ErrorState title="Could not load the recurring invoice" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Edit recurring invoice"
        breadcrumbs={[
          { label: "Recurring invoices", href: "/sales/recurring-invoices" },
          { label: "Template", href: `/sales/recurring-invoices/${id}` },
          { label: "Edit" },
        ]}
        description="Changes apply to invoices generated from now on. Drafts already generated are not changed."
      />
      <PageBody className="max-w-6xl">
        <RecurringInvoiceForm template={result.data} />
      </PageBody>
    </>
  );
}
