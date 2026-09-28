import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { BankAccountForm } from "@/features/banking/bank-account-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { BankAccount, BankTransaction } from "@/types/api/banking";

export const metadata: Metadata = { title: "Edit bank account" };

export default async function EditBankAccountPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_BANK_ACCOUNTS)) {
    return (
      <>
        <PageHeader title="Edit bank account" />
        <ForbiddenState resource="editing bank accounts" />
      </>
    );
  }

  const [result, lines] = await Promise.all([
    tryServer(() => serverApi.get<BankAccount>(`bank-accounts/${id}`)),
    // One row is enough to know whether any statement line exists — which is
    // what locks the opening balance (services/bank_accounts.py ::
    // opening_balance_locked).
    roleHasPermission(session.role, PERMISSIONS.VIEW_BANK_TRANSACTIONS)
      ? tryServer(() => serverApi.list<BankTransaction>("bank-transactions", { query: { bank_account: id, page_size: 1 } }))
      : Promise.resolve(null),
  ]);

  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit bank account" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const bankAccount = result.data;
  // Unknown (no permission, or the count failed) is treated as locked: the
  // server refuses the change either way, and a disabled field is better
  // than an offered one that is then rejected.
  const openingBalanceLocked = lines?.ok ? lines.data.count > 0 : true;

  return (
    <>
      <PageHeader
        title={`Edit ${bankAccount.name}`}
        breadcrumbs={[
          { label: "Bank accounts", href: "/banking/accounts" },
          { label: bankAccount.name, href: `/banking/accounts/${bankAccount.id}` },
          { label: "Edit" },
        ]}
        description="Descriptive details only. The kind and the ledger account are fixed once the account exists."
      />
      <PageBody className="max-w-4xl">
        <BankAccountForm bankAccount={bankAccount} openingBalanceLocked={openingBalanceLocked} />
      </PageBody>
    </>
  );
}
