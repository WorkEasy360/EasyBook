import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { BankAccountForm } from "@/features/banking/bank-account-form";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New bank account" };

const CRUMBS = [{ label: "Bank accounts", href: "/banking/accounts" }, { label: "New" }];

export default async function NewBankAccountPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_BANK_ACCOUNTS)) {
    return (
      <>
        <PageHeader title="New bank account" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating bank accounts" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="New bank account"
        breadcrumbs={CRUMBS}
        description="Creating the account posts nothing. Its ledger account keeps the book balance; imported statement lines are compared against it."
      />
      <PageBody className="max-w-4xl">
        <BankAccountForm />
      </PageBody>
    </>
  );
}
