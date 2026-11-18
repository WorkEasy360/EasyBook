import "server-only";

import { cookies } from "next/headers";
import { cookieName, cookiesAreSecure, plainCookieName, type AuthCookie } from "./cookie-names";

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
 *
 * Names: never write them here - `cookieName()` (./cookie-names.ts) gives the
 * `__Host-` form whenever cookies are Secure, so a sibling subdomain cannot
 * plant a cookie EasyBook would read.
 */

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
    secure: cookiesAreSecure(),
    // Path=/ and no Domain: what a `__Host-` cookie requires, and right for all of them.
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
  const access = store.get(cookieName("access"))?.value;
  const refresh = store.get(cookieName("refresh"))?.value;
  if (!access || !refresh) return null;
  return {
    access,
    refresh,
    organizationId: store.get(cookieName("organization"))?.value ?? null,
  };
}

export async function readOrganizationId(): Promise<string | null> {
  const store = await cookies();
  return store.get(cookieName("organization"))?.value ?? null;
}

/**
 * Only callable from a Route Handler or Server Action — Server Components are
 * not allowed to set cookies. Token rotation during a page render is handled
 * in src/middleware.ts for exactly this reason.
 */
export async function writeSession(tokens: SessionTokens): Promise<void> {
  const store = await cookies();
  const base = cookieSecurity();
  store.set(cookieName("access"), tokens.access, { ...base, maxAge: ACCESS_MAX_AGE_SECONDS });
  store.set(cookieName("refresh"), tokens.refresh, { ...base, maxAge: REFRESH_MAX_AGE_SECONDS });
  expireUnprefixed(store, ["access", "refresh"]);
}

export async function writeOrganizationId(organizationId: string): Promise<void> {
  const store = await cookies();
  store.set(cookieName("organization"), organizationId, {
    ...cookieSecurity(),
    maxAge: ORG_MAX_AGE_SECONDS,
  });
  expireUnprefixed(store, ["organization"]);
}

export async function clearSession(): Promise<void> {
  const store = await cookies();
  const base = cookieSecurity();
  // maxAge 0 rather than delete(): delete() omits the attributes, and a cookie
  // set with `secure` must be cleared with `secure` to be removed reliably.
  store.set(cookieName("access"), "", { ...base, maxAge: 0 });
  store.set(cookieName("refresh"), "", { ...base, maxAge: 0 });
  store.set(cookieName("organization"), "", { ...base, maxAge: 0 });
  expireUnprefixed(store, ["access", "refresh", "organization"]);
}

type CookieStore = Awaited<ReturnType<typeof cookies>>;

/**
 * Cookies are `__Host-` prefixed when Secure (./cookie-names.ts), so the
 * session an earlier version left under the plain names is never read again -
 * people are signed out of EasyBook once on the deploy that introduced the
 * prefix, and come back silently through Workeasy360. This also expires those
 * leftovers (a refresh token the browser would otherwise keep for days) the
 * next time a session is written or ended. It can only touch a cookie this
 * host set itself; one a sibling planted with a Domain is not ours to remove,
 * and is not read either.
 */
function expireUnprefixed(store: CookieStore, which: AuthCookie[]): void {
  if (!cookiesAreSecure()) return;
  const base = cookieSecurity();
  for (const cookie of which) store.set(plainCookieName(cookie), "", { ...base, maxAge: 0 });
}

/**
 * Leaves the "signed out of EasyBook in this browser" marker
 * (src/lib/auth/direct-visit.ts): until the next sign-in, a visit with no
 * session shows the sign-in page instead of being sent to Workeasy360 by
 * itself, so signing out never ends in a silent sign-in. A SESSION cookie -
 * no maxAge - so every tab and the proxy see it for one browser session.
 * Both sign-outs call this.
 */
export async function markSignedOut(): Promise<void> {
  const store = await cookies();
  store.set(cookieName("signedOut"), "1", cookieSecurity());
}

/**
 * A successful sign-in ends the sign-out marker, and ONLY that: the loop
 * guard (`eb_sso_auto`) is left to expire by itself after its 60 s. If the
 * response that writes the session were also to delete the guard, a browser
 * that drops the new session cookies would be sent round again on its next
 * navigation - the very loop the guard exists to stop
 * (src/lib/auth/direct-visit.ts). Called wherever a session is written.
 */
export async function clearSignedOutMarker(): Promise<void> {
  const store = await cookies();
  store.set(cookieName("signedOut"), "", { ...cookieSecurity(), maxAge: 0 });
}
