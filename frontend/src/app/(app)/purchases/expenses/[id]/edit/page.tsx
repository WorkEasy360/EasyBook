import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { ExpenseForm } from "@/features/purchases/expense-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Expense } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Edit expense" };

export default async function EditExpensePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_EXPENSES)) {
    return (
      <>
        <PageHeader title="Edit expense" />
        <ForbiddenState resource="editing expenses" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Expense>(`purchases/expenses/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit expense" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (expense_not_draft). A posted expense is
  // corrected by voiding it and recording a new one.
  if (result.data.status !== "draft") redirect(`/purchases/expenses/${id}`);

  return (
    <>
      <PageHeader
        title="Edit draft expense"
        breadcrumbs={[
          { label: "Expenses", href: "/purchases/expenses" },
          { label: "Draft expense", href: `/purchases/expenses/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-5xl">
        <ExpenseForm expense={result.data} />
      </PageBody>
    </>
  );
}
