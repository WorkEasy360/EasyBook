import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { RuleForm } from "@/features/automation/rule-form";
import { loadCatalogs, loadMembers, memberOptions } from "@/features/automation/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";

export const metadata: Metadata = { title: "New automation rule" };

const CRUMBS = [{ label: "Automation", href: "/automation" }, { label: "New rule" }];

export default async function NewAutomationRulePage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.CREATE_AUTOMATION)) {
    return (
      <>
        <PageHeader title="New automation rule" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating automation rules" />
      </>
    );
  }

  const [[triggers, actions], members] = await Promise.all([loadCatalogs(), loadMembers()]);

  // Without the catalogs there is nothing truthful to offer: every trigger
  // and action choice comes from them.
  const failed = !triggers.ok ? triggers.error : !actions.ok ? actions.error : null;
  if (failed || !triggers.ok || !actions.ok) {
    return (
      <>
        <PageHeader title="New automation rule" breadcrumbs={CRUMBS} />
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
      <PageHeader
        title="New automation rule"
        breadcrumbs={CRUMBS}
        description="Saved as a draft. Nothing runs until the rule is activated."
      />
      <PageBody className="max-w-5xl">
        <RuleForm
          triggers={triggers.data}
          catalog={actions.data}
          members={memberOptions(members)}
          canManageWebhooks={roleHasPermission(session.role, PERMISSIONS.MANAGE_AUTOMATION_WEBHOOKS)}
        />
      </PageBody>
    </>
  );
}
