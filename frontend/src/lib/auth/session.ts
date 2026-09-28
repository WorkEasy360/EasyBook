import "server-only";

import { cookies } from "next/headers";

/**
 * Session storage (spec §12, §80).
 *
 * Tokens live in httpOnly cookies and are attached to Django calls by the
 * server — the browser never holds them and no JS on the page can read them,
 * so an XSS cannot exfiltrate a session. This is the reason the app talks to
 * Django through the BFF route handlers (src/app/api/bff) instead of calling
 * it directly with an Authorization header assembled in the client.
 *
 * SameSite=Lax is the right default here: the BFF is same-origin, so no
 * legitimate cross-site request needs the cookie, and Lax still survives the
 * top-level navigation back from an OAuth-style redirect if one is added.
 */

export const ACCESS_COOKIE = "eb_at";
export const REFRESH_COOKIE = "eb_rt";
export const ORG_COOKIE = "eb_org";

/** Mirrors SIMPLE_JWT.REFRESH_TOKEN_LIFETIME (7 days) in the backend. */
const REFRESH_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;
/**
 * The access cookie outlives the token itself on purpose: the value is still
 * needed after expiry to know WHICH session to refresh. Expiry is decided by
 * the token's own `exp`, not by the cookie.
 */
const ACCESS_MAX_AGE_SECONDS = REFRESH_MAX_AGE_SECONDS;
export const ORG_MAX_AGE_SECONDS = 180 * 24 * 60 * 60;

export function cookieSecurity() {
  return {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.SESSION_COOKIE_SECURE === "1",
    path: "/",
  } as const;
}

export interface SessionTokens {
  access: string;
  refresh: string;
}

export interface Session extends SessionTokens {
  organizationId: string | null;
}

export async function readSession(): Promise<Session | null> {
  const store = await cookies();
  const access = store.get(ACCESS_COOKIE)?.value;
  const refresh = store.get(REFRESH_COOKIE)?.value;
  if (!access || !refresh) return null;
  return {
    access,
    refresh,
    organizationId: store.get(ORG_COOKIE)?.value ?? null,
  };
}

export async function readOrganizationId(): Promise<string | null> {
  const store = await cookies();
  return store.get(ORG_COOKIE)?.value ?? null;
}

/**
 * Only callable from a Route Handler or Server Action — Server Components are
 * not allowed to set cookies. Token rotation during a page render is handled
 * in src/middleware.ts for exactly this reason.
 */
export async function writeSession(tokens: SessionTokens): Promise<void> {
  const store = await cookies();
  const base = cookieSecurity();
  store.set(ACCESS_COOKIE, tokens.access, { ...base, maxAge: ACCESS_MAX_AGE_SECONDS });
  store.set(REFRESH_COOKIE, tokens.refresh, { ...base, maxAge: REFRESH_MAX_AGE_SECONDS });
}

export async function writeOrganizationId(organizationId: string): Promise<void> {
  const store = await cookies();
  store.set(ORG_COOKIE, organizationId, {
    ...cookieSecurity(),
    maxAge: ORG_MAX_AGE_SECONDS,
  });
}

export async function clearSession(): Promise<void> {
  const store = await cookies();
  const base = cookieSecurity();
  // maxAge 0 rather than delete(): delete() omits the attributes, and a cookie
  // set with `secure` must be cleared with `secure` to be removed reliably.
  store.set(ACCESS_COOKIE, "", { ...base, maxAge: 0 });
  store.set(REFRESH_COOKIE, "", { ...base, maxAge: 0 });
  store.set(ORG_COOKIE, "", { ...base, maxAge: 0 });
}
