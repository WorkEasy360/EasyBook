"use client";

import * as React from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { useOrg } from "@/components/providers/org-provider";
import { useLookup } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { queryKeys } from "@/lib/api/query-keys";
import type { ApiError } from "@/lib/api/errors";
import type { Membership } from "@/types/api/accounts";
import type { Task } from "@/types/api/projects";

/**
 * Pickers local to projects: a project's tasks, and the organization's people.
 *
 * Neither list endpoint can search, so both filter what they fetched (the
 * same limitation, and the same honest empty message, as
 * features/shared/pickers.tsx).
 */

const NO_SERVER_SEARCH = "No match in the loaded list.";

function localFilter<T>(rows: T[], term: string, fields: (row: T) => Array<string | null | undefined>): T[] {
  const needle = term.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((row) => fields(row).some((field) => (field ?? "").toLowerCase().includes(needle)));
}

/**
 * GET /organizations/members/ — a BARE ARRAY of active memberships (not a
 * page), each with the nested user. Captured live.
 */
export function useOrgMembers(): UseQueryResult<Membership[], ApiError> {
  const { organizationId } = useOrg();
  return useQuery<Membership[], ApiError>({
    queryKey: [...queryKeys.resource(organizationId, "organizations/members"), "all"],
    queryFn: () => api.get<Membership[]>("organizations/members"),
    staleTime: 120_000,
  });
}

/** "Priya Staff" or the email when no name is set. */
export function memberName(membership: Pick<Membership, "user">): string {
  const full = `${membership.user.first_name} ${membership.user.last_name}`.trim();
  return full || membership.user.email;
}

/**
 * Active tasks of one project (GET /projects/{id}/tasks/?is_active=true).
 * Time cannot be logged against an inactive task (`task_inactive`), so those
 * are not offered.
 */
export function TaskPicker({
  projectId,
  value,
  onChange,
  disabled,
  onSelectTask,
}: {
  projectId: string | null;
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  onSelectTask?: (task: Task | null) => void;
}) {
  const [search, setSearch] = React.useState("");
  const query = useLookup<Task>(`projects/${projectId ?? "none"}/tasks`, "tasks", "", {
    enabled: Boolean(projectId),
    extraParams: { page_size: 200, is_active: "true" },
  });

  const rows = projectId ? (query.data?.results ?? []) : [];
  const filtered = localFilter(rows, search, (row) => [row.name]);
  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => {
        onChange(next);
        onSelectTask?.(rows.find((row) => row.id === next) ?? null);
      }}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.id,
        label: row.name,
        hint: row.is_billable ? "Billable" : "Non-billable",
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={Boolean(projectId) && query.isLoading}
      placeholder={projectId ? "Choose a task…" : "Choose a project first"}
      emptyMessage={rows.length === 0 ? "This project has no active tasks" : NO_SERVER_SEARCH}
      selectedLabel={selected?.name ?? null}
      disabled={disabled || !projectId}
    />
  );
}

/** Active members of the organization, optionally excluding some user ids. */
export function MemberPicker({
  value,
  onChange,
  disabled,
  exclude = [],
  placeholder,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  /** User ids not to offer, e.g. people already on the project. */
  exclude?: readonly string[];
  placeholder?: string;
}) {
  const [search, setSearch] = React.useState("");
  const query = useOrgMembers();
  const rows = (query.data ?? []).filter((row) => row.is_active && !exclude.includes(row.user.id));
  const filtered = localFilter(rows, search, (row) => [memberName(row), row.user.email]);
  const selected = (query.data ?? []).find((row) => row.user.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => onChange(next)}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.user.id,
        label: memberName(row),
        hint: row.user.email,
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={placeholder ?? "Choose a person…"}
      emptyMessage={rows.length === 0 ? "No one else to add" : NO_SERVER_SEARCH}
      selectedLabel={selected ? memberName(selected) : null}
      {...(disabled === undefined ? {} : { disabled })}
    />
  );
}
