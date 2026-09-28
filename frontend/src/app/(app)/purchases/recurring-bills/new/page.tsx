import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { RecurringBillForm } from "@/features/purchases/recurring-bill-form";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New recurring bill" };

const CRUMBS = [{ label: "Recurring bills", href: "/purchases/recurring-bills" }, { label: "New" }];

/** `?vendor=<id>` preselects the vendor. */
export default async function NewRecurringBillPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_RECURRING_BILLS)) {
    return (
      <>
        <PageHeader title="New recurring bill" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="managing recurring bills" />
      </>
    );
  }

  const vendorId = paramOf(params, "vendor");

  return (
    <>
      <PageHeader
        title="New recurring bill"
        breadcrumbs={CRUMBS}
        description="Each run creates a draft bill to review and post. Nothing is posted automatically."
      />
      <PageBody className="max-w-6xl">
        <RecurringBillForm {...(vendorId ? { vendorId } : {})} />
      </PageBody>
    </>
  );
}
