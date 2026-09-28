import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { JournalForm } from "@/features/accounting/journal-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { JournalEntry } from "@/types/api/accounting";

export const metadata: Metadata = { title: "Edit journal" };

export default async function EditJournalPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Edit journal" />
        <ForbiddenState resource="editing journals" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<JournalEntry>(`accounting/journals/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit journal" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  // Only drafts are editable (`journal_not_draft`). Posted journals are
  // immutable in the model itself; they are corrected by reversal.
  if (result.data.status !== "draft") redirect(`/accounting/journals/${id}`);

  return (
    <>
      <PageHeader
        title="Edit draft journal"
        breadcrumbs={[
          { label: "Journals", href: "/accounting/journals" },
          { label: "Draft journal", href: `/accounting/journals/${id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <JournalForm journal={result.data} />
      </PageBody>
    </>
  );
}
