import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { RuleForm } from "@/features/automation/rule-form";
import { loadCatalogs, loadMembers, memberOptions } from "@/features/automation/server";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { AutomationRule } from "@/types/api/automation";

export const metadata: Metadata = { title: "Edit automation rule" };

export default async function EditAutomationRulePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.EDIT_AUTOMATION)) {
    return (
      <>
        <PageHeader title="Edit automation rule" />
        <ForbiddenState resource="editing automation rules" />
      </>
    );
  }

  const [result, [triggers, actions], members] = await Promise.all([
    tryServer(() => serverApi.get<AutomationRule>(`automation/rules/${id}`)),
    loadCatalogs(),
    loadMembers(),
  ]);

  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit automation rule" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const rule = result.data;
  // services/rules.py :: update_rule refuses archived rules
  // (automation_rule_archived); draft, active and paused rules are editable.
  if (rule.status === "archived") redirect(`/automation/rules/${id}`);

  const crumbs = [
    { label: "Automation", href: "/automation" },
    { label: rule.name, href: `/automation/rules/${id}` },
    { label: "Edit" },
  ];

  const failed = !triggers.ok ? triggers.error : !actions.ok ? actions.error : null;
  if (failed || !triggers.ok || !actions.ok) {
    return (
      <>
        <PageHeader title="Edit automation rule" breadcrumbs={crumbs} />
        <ErrorState
          title="Could not load the automation catalog"
          message={failed?.message ?? "The trigger and action catalog is unavailable."}
          reference={referenceOf(failed)}
        />
      </>
    );
  }

  return (
    <>
      <PageHeader title={`Edit ${rule.name}`} breadcrumbs={crumbs} description={`Version ${rule.version}.`} />
      <PageBody className="max-w-5xl">
        <RuleForm
          rule={rule}
          triggers={triggers.data}
          catalog={actions.data}
          members={memberOptions(members)}
          canManageWebhooks={roleHasPermission(session.role, PERMISSIONS.MANAGE_AUTOMATION_WEBHOOKS)}
        />
      </PageBody>
    </>
  );
}
