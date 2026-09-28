import "server-only";

import { serverApi, tryServer } from "@/lib/api/server";
import { indexList } from "@/lib/api/lookups";
import type { Membership } from "@/types/api/accounts";
import type { Project, Task, TimeEntry } from "@/types/api/projects";

/**
 * Names for the ids a time entry carries (project, task, user).
 *
 * Tasks have no flat endpoint — only /projects/{id}/tasks/ — so they are
 * fetched per distinct project on the page. People come from
 * /organizations/members/ (a bare array), which is only needed when the
 * viewer can see other people's time.
 */
export interface TimeLookups {
  projects: Map<string, { name: string; project_code: string; currency: string }>;
  tasks: Map<string, string>;
  people: Map<string, string>;
  memberships: Membership[];
}

export function membershipLabel(membership: Membership): string {
  return `${membership.user.first_name} ${membership.user.last_name}`.trim() || membership.user.email;
}

export async function loadTimeLookups(
  entries: readonly TimeEntry[],
  { includePeople, extraProjectIds = [] }: { includePeople: boolean; extraProjectIds?: readonly string[] },
): Promise<TimeLookups> {
  const projectIds = [...new Set([...entries.map((entry) => entry.project), ...extraProjectIds])];

  const [projects, taskPages, members] = await Promise.all([
    indexList<Project>("projects", projectIds),
    Promise.all(
      [...new Set(entries.map((entry) => entry.project))].map((projectId) =>
        tryServer(() => serverApi.list<Task>(`projects/${projectId}/tasks`, { query: { page_size: 200 } })),
      ),
    ),
    includePeople ? tryServer(() => serverApi.get<Membership[]>("organizations/members")) : Promise.resolve(null),
  ]);

  const tasks = new Map<string, string>();
  for (const page of taskPages) {
    if (page.ok) for (const task of page.data.results) tasks.set(task.id, task.name);
  }

  const memberships = members?.ok ? members.data : [];
  return {
    projects: new Map(
      [...projects.values()].map((project) => [
        project.id,
        { name: project.name, project_code: project.project_code, currency: project.currency },
      ]),
    ),
    tasks,
    people: new Map(memberships.map((membership) => [membership.user.id, membershipLabel(membership)])),
    memberships,
  };
}
