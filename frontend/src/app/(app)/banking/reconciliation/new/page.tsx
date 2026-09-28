import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { ReconciliationForm } from "@/features/banking/reconciliation-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { BankAccount } from "@/types/api/banking";

export const metadata: Metadata = { title: "Start reconciliation" };

const CRUMBS = [{ label: "Reconciliation", href: "/banking/reconciliation" }, { label: "New" }];

/** `?bank_account=<id>` preselects the account (from an account page). */
export default async function NewReconciliationPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.RECONCILE_BANK)) {
    return (
      <>
        <PageHeader title="Start reconciliation" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="reconciling bank accounts" />
      </>
    );
  }

  const accountId = paramOf(params, "bank_account");
  const account = accountId ? await tryServer(() => serverApi.get<BankAccount>(`bank-accounts/${accountId}`)) : null;

  return (
    <>
      <PageHeader
        title="Start reconciliation"
        breadcrumbs={CRUMBS}
        description="Compare one bank statement period with the lines explained in EasyBook. Starting posts and locks nothing."
      />
      <PageBody className="max-w-3xl">
        <ReconciliationForm initialAccount={account?.ok ? account.data : undefined} />
      </PageBody>
    </>
  );
}
