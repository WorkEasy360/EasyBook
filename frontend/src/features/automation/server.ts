import "server-only";

import { serverApi, tryServer } from "@/lib/api/server";
import { displayName, type Membership } from "@/types/api/accounts";
import type { ActionCatalogEntry, TriggerCatalogEntry } from "@/types/api/automation";
import type { MemberOption } from "./rule-form";

/**
 * Server-side loads shared by the automation pages.
 *
 * Both catalogs are bare arrays gated by automation.view (catalog views in
 * automation/api/views.py). They are fetched per request, never cached across
 * tenants, and never hard-coded.
 */
export function loadCatalogs() {
  return Promise.all([
    tryServer(() => serverApi.get<TriggerCatalogEntry[]>("automation/catalog/triggers")),
    tryServer(() => serverApi.get<ActionCatalogEntry[]>("automation/catalog/actions")),
  ]);
}

/**
 * GET organizations/members/ — a bare array of ACTIVE memberships, readable by
 * any member (MembershipListView has no permission gate). Used to name the
 * people an execution ran as and the recipient of a notification action.
 */
export async function loadMembers(): Promise<Membership[]> {
  const result = await tryServer(() => serverApi.get<Membership[]>("organizations/members"));
  return result.ok ? result.data : [];
}

export function memberOptions(members: readonly Membership[]): MemberOption[] {
  return members.map((member) => ({ id: member.user.id, label: `${displayName(member.user)} (${member.user.email})` }));
}

export function memberName(members: readonly Membership[], userId: string | null | undefined, fallback = "—"): string {
  if (!userId) return fallback;
  const member = members.find((row) => row.user.id === userId);
  return member ? displayName(member.user) : "A former member";
}
