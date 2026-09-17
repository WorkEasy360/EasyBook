import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { ProjectForm } from "@/features/projects/project-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Project } from "@/types/api/projects";

export const metadata: Metadata = { title: "Edit project" };

/**
 * ProjectDetailView.update applies no status rule, so a project is editable
 * in any status; what is fixed is the customer, code, currency and billing
 * method (ProjectUpdateSerializer does not read them), shown disabled.
 */
export default async function EditProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_PROJECTS)) {
    return (
      <>
        <PageHeader title="Edit project" />
        <ForbiddenState resource="editing projects" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Project>(`projects/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit project" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const project = result.data;

  return (
    <>
      <PageHeader
        title={`Edit ${project.name}`}
        breadcrumbs={[
          { label: "Projects", href: "/projects" },
          { label: project.project_code, href: `/projects/${project.id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-5xl">
        <ProjectForm project={project} />
      </PageBody>
    </>
  );
}
