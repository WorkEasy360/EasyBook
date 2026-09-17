import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { RuleForm } from "@/features/banking/rule-form";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New bank rule" };

const CRUMBS = [{ label: "Bank rules", href: "/banking/rules" }, { label: "New" }];

export default async function NewBankRulePage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_BANK_RULES)) {
    return (
      <>
        <PageHeader title="New bank rule" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="managing bank rules" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="New bank rule"
        breadcrumbs={CRUMBS}
        description="Saving a rule changes nothing by itself. It is used when rules are applied to a statement line."
      />
      <PageBody className="max-w-4xl">
        <RuleForm />
      </PageBody>
    </>
  );
}
