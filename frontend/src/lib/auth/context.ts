import "server-only";

import { cache } from "react";
import { redirect } from "next/navigation";
import { callUpstream, decodeJson } from "@/lib/api/upstream";
import { readSession } from "./session";
import type { Organization, User } from "@/types/api/accounts";
import type { Role } from "@/lib/authz/permissions";

/**
 * The signed-in context every authenticated page needs: who the user is, which
 * organizations they belong to, and which one is active.
 *
 * React's `cache()` dedupes this per request, so the shell, the page and any
 * nested Server Component share one pair of upstream calls instead of each
 * re-fetching (spec §77: no request waterfalls).
 */

export interface SessionContext {
  user: User;
  organizations: Organization[];
  organization: Organization;
  role: Role | null;
  /** Convenience for formatters — both come off the active organization. */
  currency: string;
  timeZone: string;
}

/**
 * The backend refused the token itself. Distinct from "no answer" (null): a
 * throttled, erroring or unreachable API says nothing about the session.
 */
const SESSION_REJECTED = Symbol("session-rejected");

async function fetchJson<T>(path: string, accessToken: string): Promise<T | null | typeof SESSION_REJECTED> {
  const response = await callUpstream({
    path,
    headers: { Authorization: `Bearer ${accessToken}`, Accept: "application/json" },
    cache: "no-store",
  });
  if (response.status === 401) return SESSION_REJECTED;
  if (response.status !== 200) return null;
  return decodeJson(response) as T;
}


/**
 * Resolves the full session or redirects. Use in any authenticated layout or
 * page; middleware has already guaranteed a cookie exists, so a null here
 * means the tokens stopped working between the two.
 */
export const requireSession = cache(async (): Promise<SessionContext> => {
  const session = await readSession();
  if (!session) redirect("/login");

  const [user, organizations] = await Promise.all([
    fetchJson<User>("/auth/me/", session.access),
    fetchJson<Organization[]>("/organizations/", session.access),
  ]);

  if (user === SESSION_REJECTED || organizations === SESSION_REJECTED) redirect("/login?reason=expired");
  if (!user || !organizations) {
    // NOT a redirect to sign-in. The session is still valid, so the proxy
    // would send /login straight back here — an endless redirect loop the
    // moment the API is throttled or briefly down. Failing the render shows
    // the error boundary with a retry instead.
    throw new Error("EasyBook could not load your account. Please try again in a moment.");
  }

  if (organizations.length === 0) {
    // A signed-in user with no organization cannot use any org-scoped screen:
    // every request would 400 with organization_required.
    redirect("/onboarding/organization");
  }

  const active =
    organizations.find((organization) => organization.id === session.organizationId) ??
    organizations[0];

  if (!active) redirect("/onboarding/organization");

  return {
    user,
    organizations,
    organization: active,
    role: active.role,
    currency: active.default_currency,
    timeZone: active.timezone,
  };
});

/** Non-redirecting variant, for deciding what to render on a public page. */
export const maybeSession = cache(async (): Promise<SessionContext | null> => {
  const session = await readSession();
  if (!session) return null;

  const [user, organizations] = await Promise.all([
    fetchJson<User>("/auth/me/", session.access),
    fetchJson<Organization[]>("/organizations/", session.access),
  ]);
  if (!user || !organizations || user === SESSION_REJECTED || organizations === SESSION_REJECTED) return null;
  if (organizations.length === 0) return null;

  const active =
    organizations.find((organization) => organization.id === session.organizationId) ??
    organizations[0];
  if (!active) return null;

  return {
    user,
    organizations,
    organization: active,
    role: active.role,
    currency: active.default_currency,
    timeZone: active.timezone,
  };
});
