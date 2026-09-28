import "server-only";

import { NextResponse } from "next/server";
import { isForeignOriginWrite } from "@/lib/security/request-guards";
import { callUpstream, decodeJson, type UpstreamResponse } from "@/lib/api/upstream";
import { toApiError } from "@/lib/api/errors";
import { writeOrganizationId, writeSession } from "@/lib/auth/session";
import type { LoginResponse, Organization } from "@/types/api/accounts";

/**
 * The credential exchange shared by /api/auth/login and /api/auth/register.
 *
 * The token pair never leaves the server — it goes straight into httpOnly
 * cookies and the response body carries only what the UI needs to render.
 * That is the whole point of doing sign-in server-side (spec §12, §80).
 */

export function errorEnvelope(code: string, message: string, status: number): NextResponse {
  return NextResponse.json(
    { error: { code, message, details: null, request_id: crypto.randomUUID() } },
    { status },
  );
}

/**
 * Login CSRF (signing a victim into the attacker's account), forced sign-ups
 * and forced organization switches are all refused when a browser says the
 * request is cross-origin (src/lib/security/request-guards.ts).
 */
export function refuseForeignOrigin(request: Request): NextResponse | null {
  if (!isForeignOriginWrite(request.method, request.headers)) return null;
  return errorEnvelope("cross_origin_request", "This request did not come from EasyBook.", 403);
}

/** Relays an upstream failure as the documented envelope, never its raw body. */
export function relayUpstreamError(upstream: UpstreamResponse): NextResponse {
  const error = toApiError(upstream.status, decodeJson(upstream), upstream.requestId);
  return NextResponse.json(
    {
      error: {
        code: error.code,
        message: error.message,
        details: error.details,
        request_id: error.requestId ?? crypto.randomUUID(),
      },
    },
    { status: upstream.status },
  );
}

export async function signInWithPassword(
  email: string,
  password: string,
  clientIp: string | null = null,
): Promise<NextResponse> {
  // USERNAME_FIELD is "email" (backend/accounts/models.py), so SimpleJWT's
  // serializer expects `email`, not `username`.
  const upstream = await callUpstream({
    path: "/auth/login/",
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ email, password }),
    // Sign-in is throttled per client; without this every browser user shares
    // this server's own bucket (backend/core/client_ip.py).
    clientIp,
  });

  if (upstream.status !== 200) {
    // A 401 gets a fixed message rather than the upstream one: SimpleJWT's
    // wording can differ between "no such account" and "wrong password",
    // which is a user-enumeration signal.
    if (upstream.status === 401) {
      return errorEnvelope("no_active_account", "That email address and password do not match an account.", 401);
    }
    if (upstream.status === 429) {
      // backend throttle scope "auth" is 20/min (config/settings/base.py).
      return errorEnvelope("throttled", "Too many sign-in attempts. Please wait a minute and try again.", 429);
    }
    return relayUpstreamError(upstream);
  }

  const tokens = decodeJson(upstream) as LoginResponse | null;
  if (!tokens?.access || !tokens?.refresh) {
    return errorEnvelope("upstream_error", "Sign-in failed unexpectedly. Please try again.", 502);
  }

  await writeSession(tokens);

  // Select an organization immediately: without X-Organization-Id every
  // org-scoped call 400s with organization_required, so landing on the
  // dashboard with none chosen would show a wall of errors.
  const orgResponse = await callUpstream({
    path: "/organizations/",
    headers: { Authorization: `Bearer ${tokens.access}`, Accept: "application/json" },
    clientIp,
    cache: "no-store",
  });
  const organizations =
    orgResponse.status === 200 ? ((decodeJson(orgResponse) as Organization[] | null) ?? []) : [];

  const first = organizations[0];
  if (first) await writeOrganizationId(first.id);

  return NextResponse.json({
    organizations: organizations.map((organization) => ({
      id: organization.id,
      name: organization.name,
      role: organization.role,
    })),
    redirectTo: organizations.length > 0 ? "/dashboard" : "/onboarding/organization",
  });
}
