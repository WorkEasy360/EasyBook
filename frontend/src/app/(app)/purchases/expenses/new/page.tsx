import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { ExpenseForm } from "@/features/purchases/expense-form";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New expense" };

const CRUMBS = [{ label: "Expenses", href: "/purchases/expenses" }, { label: "New" }];

/** `?vendor=<id>` preselects the vendor. */
export default async function NewExpensePage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_EXPENSES)) {
    return (
      <>
        <PageHeader title="New expense" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="recording expenses" />
      </>
    );
  }

  const vendorId = paramOf(params, "vendor");

  return (
    <>
      <PageHeader
        title="New expense"
        breadcrumbs={CRUMBS}
        description="Saved as a draft. Nothing is recorded in the ledger until the expense is posted."
      />
      <PageBody className="max-w-5xl">
        <ExpenseForm {...(vendorId ? { vendorId } : {})} />
      </PageBody>
    </>
  );
}
