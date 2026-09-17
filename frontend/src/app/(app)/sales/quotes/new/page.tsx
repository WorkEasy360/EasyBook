import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { QuoteForm } from "@/features/sales/quote-form";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New quote" };

const CRUMBS = [{ label: "Quotes", href: "/sales/quotes" }, { label: "New" }];

/** `?customer=<id>` preselects the customer (from a customer page). */
export default async function NewQuotePage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_QUOTES)) {
    return (
      <>
        <PageHeader title="New quote" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating quotes" />
      </>
    );
  }

  const customerId = paramOf(params, "customer");

  return (
    <>
      <PageHeader
        title="New quote"
        breadcrumbs={CRUMBS}
        description="Saved as a draft with its quote number. Nothing is recorded in the ledger at any stage of a quote."
      />
      <PageBody className="max-w-6xl">
        <QuoteForm {...(customerId ? { initialCustomerId: customerId } : {})} />
      </PageBody>
    </>
  );
}
