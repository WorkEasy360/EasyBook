import { sameSitePath } from "@/lib/redirect";

/**
 * Someone opens EasyBook directly (Workeasy360 integration guide §6b).
 *
 * A person already signed in to Workeasy360 who types EasyBook's address must
 * arrive signed in, with no sign-in page and no password. When EasyBook is
 * SSO-only, a page navigation from a browser with no session therefore goes
 * straight to `/api/auth/workeasy360/start` instead of `/login`
 * (src/proxy.ts). Everything here is the policy for when that is allowed; the
 * proxy is the only caller. Pure functions and names, so both the proxy and
 * the cookie writers (src/lib/auth/session.ts) can import it, and it can be
 * tested without a server.
 *
 * Starting a sign-in by itself is never allowed:
 *  - on the sign-in pages (they are public paths; the proxy never gets here);
 *  - after the person signed out of EasyBook in this browser (the `signedOut`
 *    cookie, until the next sign-in);
 *  - when this browser already started automatically less than
 *    AUTO_START_GUARD_SECONDS ago and still has no session (the `autoStart`
 *    cookie: the loop guard).
 *
 * Cookie names are src/lib/auth/cookie-names.ts's: `eb_signed_out` and
 * `eb_sso_auto`, under a `__Host-` prefix whenever cookies are Secure, so a
 * sibling subdomain cannot plant or erase them.
 *
 * The sign-out marker is a SESSION cookie (no Max-Age): it lasts one browser
 * session, is visible to every tab and to the proxy, and holds nothing but
 * "1". Both sign-outs leave it, a successful sign-in clears it. It suppresses
 * automatic starts only; it never blocks a sign-in the person asks for.
 *
 * The loop guard holds the time (ms) the proxy last started a sign-in
 * automatically, set on that very redirect with a 60 s Max-Age. A browser that
 * comes back with no session inside the window (a dropped cookie, a failure
 * that was not reported as ?sso_error=) is shown the sign-in page instead of
 * being sent round again. A sign-in does NOT clear it: it expires by itself,
 * so a browser that drops the session it was just given is still caught.
 */

/** How long after an automatic start another one is refused. Also the guard cookie's Max-Age. */
export const AUTO_START_GUARD_SECONDS = 60;

/** The route that starts a sign-in (src/app/api/auth/workeasy360/start). */
export const SSO_START_PATH = "/api/auth/workeasy360/start";

/**
 * True for a browser loading a page (a document navigation), false for
 * everything else that can reach a page URL: Next's own client-side
 * navigations and prefetches (they ask for the page's payload and follow a
 * redirect with fetch(), which a cross-site redirect would break), fetches,
 * frames, and speculative prefetches. API routes never get here at all
 * (src/proxy.ts matcher); this keeps the same promise for page URLs.
 *
 * Sec-Fetch-* is sent by every modern browser over HTTPS and to loopback
 * addresses. Without it (a plain-HTTP address, an old browser) the Accept
 * header decides: a navigation asks for text/html, a fetch() asks for any type.
 */
export function isPageNavigation(method: string, headers: Headers): boolean {
  if (method !== "GET") return false;
  if (headers.has("rsc") || headers.has("next-router-prefetch") || headers.has("next-router-state-tree")) return false;
  const purpose = `${headers.get("sec-purpose") ?? ""} ${headers.get("purpose") ?? ""}`.toLowerCase();
  if (purpose.includes("prefetch") || purpose.includes("prerender")) return false;

  const mode = headers.get("sec-fetch-mode");
  const destination = headers.get("sec-fetch-dest");
  if (mode !== null || destination !== null) {
    return (mode === null || mode === "navigate") && (destination === null || destination === "document");
  }
  return (headers.get("accept") ?? "").toLowerCase().includes("text/html");
}

export type AutoStartRefusal = "signed_out" | "recent_auto_start";

/**
 * Why this browser must be shown the sign-in page instead of being sent to
 * Workeasy360 by itself, or null when starting is allowed. `autoStarted` is
 * the loop-guard cookie's value; one that is present but unreadable counts as
 * recent (the safe reading: a sign-in page, never another round trip).
 */
export function autoStartRefusal(
  cookies: { signedOut?: string | null; autoStarted?: string | null },
  now: number,
): AutoStartRefusal | null {
  if (cookies.signedOut) return "signed_out";
  if (cookies.autoStarted) {
    const startedAt = Number(cookies.autoStarted);
    const stale = Number.isFinite(startedAt) && now - startedAt >= AUTO_START_GUARD_SECONDS * 1000;
    if (!stale) return "recent_auto_start";
  }
  return null;
}

/**
 * The start route for the page that was asked for. `next` is carried only
 * when it is a same-site path (the one check every redirect goes through,
 * src/lib/redirect.ts) - anything else drops it, so sign-in lands on the
 * dashboard rather than anywhere a crafted URL chose. The home page has
 * nothing to return to. No organization hint: that is the launcher's.
 */
export function autoStartPath(pathname: string, search: string): string {
  const next = pathname === "/" ? null : sameSitePath(`${pathname}${search}`);
  return next ? `${SSO_START_PATH}?${new URLSearchParams({ next }).toString()}` : SSO_START_PATH;
}
