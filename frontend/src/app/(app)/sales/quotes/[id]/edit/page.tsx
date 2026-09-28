import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { QuoteForm } from "@/features/sales/quote-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Quote } from "@/types/api/sales";

export const metadata: Metadata = { title: "Edit quote" };

export default async function EditQuotePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_QUOTES)) {
    return (
      <>
        <PageHeader title="Edit quote" />
        <ForbiddenState resource="editing quotes" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Quote>(`sales/quotes/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit quote" />
        <ErrorState title="Could not load the quote" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (quote_not_draft). Once sent, a quote's terms
  // are what the customer saw; a changed offer is a new quote.
  if (result.data.status !== "draft") redirect(`/sales/quotes/${id}`);

  const quote = result.data;
  return (
    <>
      <PageHeader
        title={`Edit ${quote.quote_number}`}
        breadcrumbs={[
          { label: "Quotes", href: "/sales/quotes" },
          { label: quote.quote_number, href: `/sales/quotes/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <QuoteForm quote={quote} />
      </PageBody>
    </>
  );
}
