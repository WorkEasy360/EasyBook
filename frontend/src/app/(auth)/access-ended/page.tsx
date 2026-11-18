import type { Metadata } from "next";
import { AuthCard } from "@/features/auth/auth-card";
import { AccessEndedActions } from "@/features/auth/access-ended-actions";
import { accessEndedCopy } from "@/features/auth/access-ended-copy";
import { callUpstream, decodeJson } from "@/lib/api/upstream";
import { readOrganizationId, readSession } from "@/lib/auth/session";
import type { Organization } from "@/types/api/accounts";

export const metadata: Metadata = { title: "Access ended" };

/**
 * Where any page goes when Workeasy360 says access to EasyBook has ended -
 * the organization's trial ran out, or its subscription lapsed or was
 * cancelled, or this person's membership was removed or seat unassigned
 * (backend/sso/entitlement.py). Still signed in: the person can try again
 * once it is restored, or sign out.
 */
/**
 * The person's other organizations, for "Switch organization". The organization list is not
 * organization-scoped, so it still answers while this organization's access is refused. Empty (nothing
 * offered) for a single organization, a missing session or any failure.
 */
async function otherOrganizations() {
  const session = await readSession();
  if (!session) return [];
  const response = await callUpstream({
    path: "/organizations/",
    headers: { Authorization: `Bearer ${session.access}`, Accept: "application/json" },
    cache: "no-store",
  });
  if (response.status !== 200) return [];
  const organizations = (decodeJson(response) as Organization[] | null) ?? [];
  const active = await readOrganizationId();
  return organizations.filter((organization) => organization.id !== active).map(({ id, name }) => ({ id, name }));
}

export default async function AccessEndedPage({ searchParams }: { searchParams: Promise<{ reason?: string }> }) {
  const reason = (await searchParams).reason;
  // reason=unverified: Workeasy360 refused EasyBook's own check (a credential or
  // integration problem), so the access cannot be confirmed - not "ended".
  const copy = accessEndedCopy(reason);
  const others = await otherOrganizations();
  return (
    <AuthCard title={copy.title} description={copy.description}>
      <AccessEndedActions otherOrganizations={others} signInRequired={reason === "sign_in_required"} />
    </AuthCard>
  );
}
