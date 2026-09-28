import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { RecurringBillForm } from "@/features/purchases/recurring-bill-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { RecurringBillTemplate } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Edit recurring bill" };

/**
 * A template has no draft/posted lifecycle — RecurringBillTemplateDetailView
 * accepts an update at any time — so there is no status redirect here. Bills
 * already generated from it are separate documents and are not changed.
 */
export default async function EditRecurringBillPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_RECURRING_BILLS)) {
    return (
      <>
        <PageHeader title="Edit recurring bill" />
        <ForbiddenState resource="managing recurring bills" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<RecurringBillTemplate>(`purchases/recurring-bills/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit recurring bill" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Edit recurring bill"
        breadcrumbs={[
          { label: "Recurring bills", href: "/purchases/recurring-bills" },
          { label: "Recurring bill", href: `/purchases/recurring-bills/${id}` },
          { label: "Edit" },
        ]}
        description="Changes apply to bills generated from now on. Bills already generated are not changed."
      />
      <PageBody className="max-w-6xl">
        <RecurringBillForm template={result.data} />
      </PageBody>
    </>
  );
}
