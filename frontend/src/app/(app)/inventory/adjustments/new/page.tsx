import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { AdjustmentForm } from "@/features/inventory/adjustment-form";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New stock adjustment" };

const CRUMBS = [
  { label: "Stock adjustments", href: "/inventory/adjustments" },
  { label: "New" },
];

export default async function NewAdjustmentPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.ADJUST_INVENTORY)) {
    return (
      <>
        <PageHeader title="New stock adjustment" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="adjusting stock" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="New stock adjustment"
        breadcrumbs={CRUMBS}
        description="Saved as a draft. Stock does not change until the adjustment is posted."
      />
      <PageBody className="max-w-5xl">
        <AdjustmentForm />
      </PageBody>
    </>
  );
}
