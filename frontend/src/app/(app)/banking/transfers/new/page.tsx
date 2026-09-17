import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { TransferForm } from "@/features/banking/transfer-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { BankAccount } from "@/types/api/banking";

export const metadata: Metadata = { title: "New transfer" };

const CRUMBS = [{ label: "Transfers", href: "/banking/transfers" }, { label: "New" }];

/** `?from=<bank account id>` preselects the source (from an account page). */
export default async function NewTransferPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.RECORD_BANK_TRANSFER)) {
    return (
      <>
        <PageHeader title="New transfer" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="recording transfers" />
      </>
    );
  }

  const fromId = paramOf(params, "from");
  const from = fromId ? await tryServer(() => serverApi.get<BankAccount>(`bank-accounts/${fromId}`)) : null;

  return (
    <>
      <PageHeader
        title="New transfer"
        breadcrumbs={CRUMBS}
        description="Money moved between two of your own accounts. Saving posts the journal immediately."
      />
      <PageBody className="max-w-4xl">
        <TransferForm initialFrom={from?.ok && from.data.is_active ? from.data : undefined} />
      </PageBody>
    </>
  );
}
