import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { RecurringExpenseForm } from "@/features/purchases/recurring-expense-form";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New recurring expense" };

const CRUMBS = [{ label: "Recurring expenses", href: "/purchases/recurring-expenses" }, { label: "New" }];

/** `?vendor=<id>` preselects the vendor. */
export default async function NewRecurringExpensePage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_RECURRING_BILLS)) {
    return (
      <>
        <PageHeader title="New recurring expense" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="managing recurring expenses" />
      </>
    );
  }

  const vendorId = paramOf(params, "vendor");

  return (
    <>
      <PageHeader
        title="New recurring expense"
        breadcrumbs={CRUMBS}
        description="Each run creates a draft expense to review and post. Nothing is posted automatically."
      />
      <PageBody className="max-w-5xl">
        <RecurringExpenseForm {...(vendorId ? { vendorId } : {})} />
      </PageBody>
    </>
  );
}
