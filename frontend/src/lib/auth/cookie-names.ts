/**
 * The names (and, for the sign-in flow, the path) of every authentication
 * cookie, in one place - the proxy, the BFF, the route handlers and the
 * session helpers all go through here.
 *
 * Every product is a sibling subdomain of workeasy360.com. A cookie another
 * sibling sets with `Domain=.workeasy360.com` is sent to EasyBook too, and
 * under a plain name it would be indistinguishable from EasyBook's own (and
 * Next's cookie parser keeps the LAST of two with one name). A compromised
 * sibling could then plant its own `eb_sso` flow and send the victim to the
 * callback with the attacker's code and state - a login CSRF that passes the
 * state check - or plant `eb_at`/`eb_rt` and put the victim in the attacker's
 * session.
 *
 * Cookies whose names begin `__Host-` are accepted by a browser only when set
 * Secure, with `Path=/` and NO `Domain`, i.e. only by this exact host - and a
 * sibling cannot set one. So whenever cookies are Secure
 * (`SESSION_COOKIE_SECURE=1`, which production requires) every auth cookie
 * carries the prefix, and a cookie under the plain name is simply never read.
 * Without Secure (local development over plain http) the prefix is not
 * possible, so the plain names stay.
 *
 * Nothing here is `server-only`: the proxy and tests import it too. The
 * answer is read from the environment at call time, never frozen at import.
 */

export type AuthCookie = "access" | "refresh" | "organization" | "ssoFlow" | "signedOut" | "autoStart";

const PLAIN_NAMES: Record<AuthCookie, string> = {
  access: "eb_at",
  refresh: "eb_rt",
  organization: "eb_org",
  ssoFlow: "eb_sso",
  signedOut: "eb_signed_out",
  autoStart: "eb_sso_auto",
};

export const HOST_PREFIX = "__Host-";

/** Cookies are Secure, so the `__Host-` prefix is available - and therefore required. */
export function cookiesAreSecure(): boolean {
  return process.env.SESSION_COOKIE_SECURE === "1";
}

/** The name this cookie is set and read under right now. */
export function cookieName(which: AuthCookie): string {
  return cookiesAreSecure() ? `${HOST_PREFIX}${PLAIN_NAMES[which]}` : PLAIN_NAMES[which];
}

/**
 * The unprefixed name, for expiring what an earlier version set before the
 * prefix existed. Never read for authentication.
 */
export function plainCookieName(which: AuthCookie): string {
  return PLAIN_NAMES[which];
}
