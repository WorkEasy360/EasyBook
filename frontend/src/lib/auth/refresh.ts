import "server-only";

import { callUpstream, decodeJson } from "@/lib/api/upstream";
import type { SessionTokens } from "./session";

/**
 * Access-token refresh against /auth/refresh/.
 *
 * The backend sets ROTATE_REFRESH_TOKENS and BLACKLIST_AFTER_ROTATION
 * (config/settings/base.py), which makes this trickier than the usual
 * refresh: every successful refresh issues a NEW refresh token and
 * blacklists the one that was used. Two refreshes racing on the same token
 * means the second one is rejected and the user is signed out for no reason.
 *
 * The in-process promise map below collapses concurrent refreshes of the same
 * token into one upstream call. That covers the case this actually happens in
 * — one user's page firing several requests at once against one server
 * instance. It is not a distributed lock, and it does not need to be: if a
 * rotation really is lost across instances, the result is a 401 the user
 * recovers from by signing in again, never incorrect data.
 */

const inFlight = new Map<string, Promise<SessionTokens | null>>();

interface RefreshResponse {
  access?: unknown;
  refresh?: unknown;
}

async function performRefresh(refreshToken: string, clientIp: string | null): Promise<SessionTokens | null> {
  const response = await callUpstream({
    path: "/auth/refresh/",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh: refreshToken }),
    clientIp,
  });

  if (response.status !== 200) return null;

  const payload = decodeJson(response) as RefreshResponse | null;
  const access = typeof payload?.access === "string" ? payload.access : null;
  if (!access) return null;

  // With rotation on, a new refresh token comes back and the old one is now
  // blacklisted — persisting it is mandatory, not an optimization. Falling
  // back to the old value would only be correct if rotation were disabled.
  const refresh = typeof payload?.refresh === "string" ? payload.refresh : refreshToken;

  return { access, refresh };
}

/**
 * Refreshes, collapsing concurrent callers onto one upstream request.
 * Returns null when the refresh token is expired, blacklisted or invalid —
 * the caller should then clear the session and send the user to sign in.
 */
export function refreshTokens(refreshToken: string, clientIp: string | null = null): Promise<SessionTokens | null> {
  const existing = inFlight.get(refreshToken);
  if (existing) return existing;

  const promise = performRefresh(refreshToken, clientIp).finally(() => {
    inFlight.delete(refreshToken);
  });
  inFlight.set(refreshToken, promise);
  return promise;
}

/** Test seam — the map is module state and would otherwise leak between tests. */
export function __resetRefreshState(): void {
  inFlight.clear();
}
