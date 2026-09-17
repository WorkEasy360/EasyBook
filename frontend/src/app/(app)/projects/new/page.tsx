import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { ProjectForm } from "@/features/projects/project-form";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New project" };

const CRUMBS = [{ label: "Projects", href: "/projects" }, { label: "New" }];

/** `?customer=<id>` preselects the customer (from a customer page). */
export default async function NewProjectPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_PROJECTS)) {
    return (
      <>
        <PageHeader title="New project" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating projects" />
      </>
    );
  }

  const customerId = paramOf(params, "customer");

  return (
    <>
      <PageHeader
        title="New project"
        breadcrumbs={CRUMBS}
        description="Starts as a draft. Activate it to start logging time."
      />
      <PageBody className="max-w-5xl">
        <ProjectForm {...(customerId ? { customerId } : {})} />
      </PageBody>
    </>
  );
}
