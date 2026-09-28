import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { RuleForm } from "@/features/banking/rule-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { BankRule } from "@/types/api/banking";

export const metadata: Metadata = { title: "Edit bank rule" };

export default async function EditBankRulePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_BANK_RULES)) {
    return (
      <>
        <PageHeader title="Edit bank rule" />
        <ForbiddenState resource="managing bank rules" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<BankRule>(`bank-rules/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit bank rule" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const rule = result.data;

  return (
    <>
      <PageHeader
        title={`Edit ${rule.name}`}
        breadcrumbs={[{ label: "Bank rules", href: "/banking/rules" }, { label: rule.name }]}
        description={
          rule.vendor || rule.customer
            ? "This rule also names a customer or vendor, set outside this form. Saving keeps it."
            : "Changes apply the next time rules run. Lines already categorized by this rule are not changed."
        }
      />
      <PageBody className="max-w-4xl">
        <RuleForm rule={rule} />
      </PageBody>
    </>
  );
}
