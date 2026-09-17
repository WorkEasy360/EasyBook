import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/ui/states";
import { StatementImportWizard } from "@/features/banking/statement-import-wizard";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { BankAccount } from "@/types/api/banking";

export const metadata: Metadata = { title: "Import statement" };

export default async function ImportStatementPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.IMPORT_BANK_STATEMENT)) {
    return (
      <>
        <PageHeader title="Import statement" />
        <ForbiddenState resource="importing bank statements" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<BankAccount>(`bank-accounts/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Import statement" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const account = result.data;
  const crumbs = [
    { label: "Bank accounts", href: "/banking/accounts" },
    { label: account.name, href: `/banking/accounts/${account.id}` },
    { label: "Import statement" },
  ];

  return (
    <>
      <PageHeader
        title="Import statement"
        breadcrumbs={crumbs}
        description={`Into ${account.name}${account.masked_number ? ` (${account.masked_number})` : ""}. Lines already held for this account are skipped; nothing is posted to the ledger.`}
      />
      <PageBody className="max-w-5xl">
        {account.is_active ? (
          <StatementImportWizard bankAccountId={account.id} bankAccountName={account.name} />
        ) : (
          // services/imports.py :: bank_account_inactive
          <EmptyState
            title="This account is inactive"
            description="Statements cannot be imported into an inactive account. Reactivate it first."
            action={{ label: "Edit account", href: `/banking/accounts/${account.id}/edit` }}
          />
        )}
      </PageBody>
    </>
  );
}
