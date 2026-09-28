import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { RecurringExpenseForm } from "@/features/purchases/recurring-expense-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { RecurringExpenseTemplate } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Edit recurring expense" };

/** Templates can be updated at any time (no draft lifecycle); generated expenses are not changed. */
export default async function EditRecurringExpensePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_RECURRING_BILLS)) {
    return (
      <>
        <PageHeader title="Edit recurring expense" />
        <ForbiddenState resource="managing recurring expenses" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<RecurringExpenseTemplate>(`purchases/recurring-expenses/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit recurring expense" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Edit recurring expense"
        breadcrumbs={[
          { label: "Recurring expenses", href: "/purchases/recurring-expenses" },
          { label: result.data.description || "Recurring expense", href: `/purchases/recurring-expenses/${id}` },
          { label: "Edit" },
        ]}
        description="Changes apply to expenses generated from now on. Expenses already generated are not changed."
      />
      <PageBody className="max-w-5xl">
        <RecurringExpenseForm template={result.data} />
      </PageBody>
    </>
  );
}
