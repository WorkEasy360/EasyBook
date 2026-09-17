import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { JournalForm } from "@/features/accounting/journal-form";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New journal" };

const CRUMBS = [{ label: "Journals", href: "/accounting/journals" }, { label: "New" }];

export default async function NewJournalPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="New journal" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="recording journals" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="New journal"
        breadcrumbs={CRUMBS}
        description="Saved as a draft. Nothing reaches the ledger until the journal is posted."
      />
      <PageBody className="max-w-6xl">
        <JournalForm />
      </PageBody>
    </>
  );
}
