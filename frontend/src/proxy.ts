import { NextResponse, type NextRequest } from "next/server";
import { isNetworkError } from "@/lib/api/errors";
import { isExpiringSoon } from "@/lib/auth/jwt";
import { cookieName } from "@/lib/auth/cookie-names";
import {
  AUTO_START_GUARD_SECONDS,
  autoStartPath,
  autoStartRefusal,
  isPageNavigation,
} from "@/lib/auth/direct-visit";
import { refreshTokens } from "@/lib/auth/refresh";
import { cookieSecurity } from "@/lib/auth/session";
import { isSsoOnly } from "@/lib/auth/sso-only";
import { clientIpFromHeaders } from "@/lib/security/client-ip";
import { buildContentSecurityPolicy, generateNonce } from "@/lib/security/csp";

/**
 * Route protection and pre-render token refresh.
 *
 * This is Next 16's `proxy` file convention — the rename of `middleware`,
 * which is deprecated (nextjs.org/docs/messages/middleware-to-proxy). The
 * `config.matcher` contract is unchanged.
 *
 * Refresh lives here rather than in the server API client because a Server
 * Component cannot set cookies: it could obtain a new token pair but not
 * persist it, and with ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION on
 * the backend, losing the rotated refresh token ends the session. Middleware
 * runs before the render and CAN set cookies, so this is the one place a
 * page-load refresh can be done safely.
 *
 * Auth here is a redirect convenience, not a security control — Django
 * authorizes every request regardless of what this decides.
 *
 * A page navigation with no session goes to /login - or, when EasyBook is
 * SSO-only, straight to Workeasy360 sign-in (`autoStartRedirect` below,
 * integration guide §6b), so a person already signed in to Workeasy360 who
 * types EasyBook's address arrives signed in. The sign-in pages never do
 * that: they are public paths, answered before any of it.
 *
 * It also sets the per-request Content-Security-Policy nonce
 * (src/lib/security/csp.ts). Every response that renders a page must go
 * through `pass()` so the nonce reaches both the render (request header, which
 * Next reads to stamp its scripts) and the browser (response header).
 */

const PUBLIC_PATHS = ["/login", "/register", "/forgot-password"];
const REFRESH_MAX_AGE = 7 * 24 * 60 * 60;

/** Continue to the page, carrying the CSP nonce into the render and out to the browser. */
function pass(request: NextRequest, requestHeaders: Headers = new Headers(request.headers)): NextResponse {
  const nonce = generateNonce();
  const https =
    request.nextUrl.protocol === "https:" || request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() === "https";
  const policy = buildContentSecurityPolicy(nonce, { development: process.env.NODE_ENV === "development", https });
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

/** The Workeasy360 launcher opens `/?organization=<uuid>` (src/lib/auth/workeasy360.ts). */
const LAUNCH_ORGANIZATION = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Direct visit (integration guide §6b): when EasyBook is SSO-only, a page
 * navigation from a browser with no session at all starts the Workeasy360
 * sign-in itself, returning to the page that was asked for. Null - so the
 * caller sends the visitor to /login as always - unless every one of these
 * holds (the policy is src/lib/auth/direct-visit.ts):
 *  - it is a document navigation, not a fetch, an RSC request or a prefetch;
 *  - the person did not sign out of EasyBook in this browser (marker cookie);
 *  - this browser did not start one automatically within the last 60 s
 *    (loop guard cookie, set below);
 *  - EasyBook is SSO-only. Asked last, and cached: the backend is not called
 *    for the cases above.
 *
 * Only called when there is NO session. A session whose access ended keeps its
 * explanation (/access-ended); it is never replaced silently.
 *
 * The redirect is built the same way as the others in this file: Next turns a
 * same-origin proxy redirect into a relative Location by itself, so it never
 * names the address this server happens to listen on (src/lib/redirect.ts).
 */
async function autoStartRedirect(request: NextRequest): Promise<NextResponse | null> {
  const { pathname, search } = request.nextUrl;
  // The matcher keeps API routes out of this file; this keeps the promise if it is ever widened.
  if (pathname === "/api" || pathname.startsWith("/api/")) return null;
  if (!isPageNavigation(request.method, request.headers)) return null;
  const now = Date.now();
  const refusal = autoStartRefusal(
    {
      signedOut: request.cookies.get(cookieName("signedOut"))?.value,
      autoStarted: request.cookies.get(cookieName("autoStart"))?.value,
    },
    now,
  );
  if (refusal) return null;
  if (!(await isSsoOnly())) return null;

  const response = NextResponse.redirect(new URL(autoStartPath(pathname, search), request.url));
  // Never let a cache replay this redirect: it would skip the loop guard.
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(cookieName("autoStart"), String(now), {
    ...cookieSecurity(),
    maxAge: AUTO_START_GUARD_SECONDS,
  });
  return response;
}

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // Opened from the Workeasy360 app launcher: sign in through Workeasy360 for
  // the organization the user picked there, even if a session already exists
  // here - it may belong to another organization or another person.
  const launchOrganization = request.nextUrl.searchParams.get("organization");
  if (launchOrganization && LAUNCH_ORGANIZATION.test(launchOrganization)) {
    const target = new URL("/api/auth/workeasy360/start", request.url);
    target.searchParams.set("organization", launchOrganization);
    if (pathname !== "/") target.searchParams.set("next", pathname);
    return NextResponse.redirect(target);
  }

  // Under their `__Host-` names whenever cookies are Secure (src/lib/auth/cookie-names.ts):
  // a cookie a sibling subdomain planted under the plain name is never looked at.
  const access = request.cookies.get(cookieName("access"))?.value ?? null;
  const refresh = request.cookies.get(cookieName("refresh"))?.value ?? null;
  const hasSession = Boolean(access && refresh);

  if (isPublic(pathname)) {
    // Already signed in and heading to /login or /register: send them to the
    // app instead — unless the app sent them to /login because the backend
    // rejected the session (?reason=expired). Bouncing that back to
    // /dashboard would loop forever on cookies that look present but no
    // longer work. (A dead session on /register ends at that same
    // /login?reason=expired, which is not bounced.)
    const signedInDetour =
      pathname === "/register" || (pathname === "/login" && !request.nextUrl.searchParams.has("reason"));
    if (hasSession && signedInDetour) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
    return pass(request);
  }

  if (!hasSession) {
    const direct = await autoStartRedirect(request);
    if (direct) return direct;

    const target = new URL("/login", request.url);
    // Round-trip the intended destination so sign-in lands where they meant
    // to go. Only the path is carried, never an absolute URL, so this cannot
    // be turned into an open redirect.
    if (pathname !== "/") target.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(target);
  }

  // Refresh proactively, before the page's own data fetches start failing.
  if (access && refresh && isExpiringSoon(access)) {
    let rotated: Awaited<ReturnType<typeof refreshTokens>>;
    try {
      rotated = await refreshTokens(refresh, clientIpFromHeaders(request.headers));
    } catch (error) {
      // Backend unreachable or timed out: that says nothing about the session.
      // Clearing cookies would sign everyone out during an outage, and letting
      // the error escape breaks every navigation. Render the page; its own API
      // calls show the error state, and the next load retries the refresh.
      if (isNetworkError(error)) return pass(request);
      throw error;
    }
    if (!rotated) {
      const target = new URL("/login", request.url);
      target.searchParams.set("reason", "expired");
      const response = NextResponse.redirect(target);
      const base = cookieSecurity();
      response.cookies.set(cookieName("access"), "", { ...base, maxAge: 0 });
      response.cookies.set(cookieName("refresh"), "", { ...base, maxAge: 0 });
      response.cookies.set(cookieName("organization"), "", { ...base, maxAge: 0 });
      return response;
    }

    // Hand the fresh token to this render's Server Components by rewriting
    // the request cookie, and persist the rotated pair on the response.
    const requestCookies = new Headers(request.headers);
    const rewritten = replaceCookie(request.headers.get("cookie"), {
      [cookieName("access")]: rotated.access,
      [cookieName("refresh")]: rotated.refresh,
    });
    if (rewritten) requestCookies.set("cookie", rewritten);

    const response = pass(request, requestCookies);
    const base = cookieSecurity();
    response.cookies.set(cookieName("access"), rotated.access, { ...base, maxAge: REFRESH_MAX_AGE });
    response.cookies.set(cookieName("refresh"), rotated.refresh, { ...base, maxAge: REFRESH_MAX_AGE });
    return response;
  }

  return pass(request);
}

function replaceCookie(header: string | null, updates: Record<string, string>): string | null {
  if (!header) return null;
  const seen = new Set<string>();
  const parts = header
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const index = part.indexOf("=");
      if (index === -1) return part;
      const name = part.slice(0, index).trim();
      const replacement = updates[name];
      if (replacement === undefined) return part;
      seen.add(name);
      return `${name}=${encodeURIComponent(replacement)}`;
    });

  for (const [name, value] of Object.entries(updates)) {
    if (!seen.has(name)) parts.push(`${name}=${encodeURIComponent(value)}`);
  }
  return parts.join("; ");
}

export const config = {
  matcher: [
    /*
     * Everything except Next's own assets and the API routes. /api/bff does
     * its own 401-refresh-retry against the live upstream response, which is
     * more precise than this path's expiry guess, and /api/auth must stay
     * reachable while signed out.
     */
    "/((?!_next/static|_next/image|favicon.ico|api/|.*\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
