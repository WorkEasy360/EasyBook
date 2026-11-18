import type { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { callUpstream, decodeJson } from "@/lib/api/upstream";
import { cookieName } from "@/lib/auth/cookie-names";
import {
  clearSignedOutMarker,
  cookieSecurity,
  readSession,
  writeOrganizationId,
  writeSession,
} from "@/lib/auth/session";
import { backendErrorCode, providerErrorCode, safePath } from "@/lib/auth/workeasy360";
import { redirectToPath } from "@/lib/redirect";
import { clientIpFromHeaders } from "@/lib/security/client-ip";

/**
 * Where Workeasy360 sends the browser back: the redirect URI registered for
 * EasyBook, matched by exact string, so this path must not move.
 *
 * Django checks the state against the flow this browser kept, exchanges the
 * code and verifies the ID token (backend/sso/); this route only relays and
 * turns the result into the same httpOnly session a password sign-in gets.
 * Necessarily a cross-site GET, which is why it is not behind the
 * same-origin write guard: state, nonce and PKCE are its protection.
 */
export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const store = await cookies();
  // Only under this host's own name (`__Host-eb_sso` when Secure): a flow a sibling
  // subdomain planted under the plain name is not ours and is never read.
  const kept = parseFlowCookie(store.get(cookieName("ssoFlow"))?.value);
  // Single use, whatever happens next.
  store.set(cookieName("ssoFlow"), "", { ...cookieSecurity(), maxAge: 0 });

  // Relative redirects only: request.url is this server's own address, not
  // the one the browser used (src/lib/redirect.ts).
  const fail = (code: string) => redirectToPath(`/login?sso_error=${code}`);

  const providerError = url.searchParams.get("error");
  if (providerError) return fail(providerErrorCode(providerError));

  const code = url.searchParams.get("code") ?? "";
  const state = url.searchParams.get("state") ?? "";
  if (!kept?.flow) return fail("expired");
  if (!code || !state) return fail("failed");

  let result: { access?: unknown; refresh?: unknown; organization_id?: unknown; error?: { code?: string } } | null;
  let status: number;
  try {
    const upstream = await callUpstream({
      path: "/auth/workeasy360/callback/",
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ code, state, flow: kept.flow }),
      clientIp: clientIpFromHeaders(request.headers),
    });
    status = upstream.status;
    result = decodeJson(upstream) as typeof result;
  } catch {
    return fail("unavailable");
  }

  if (status !== 200) return fail(backendErrorCode(result?.error?.code));
  if (typeof result?.access !== "string" || typeof result.refresh !== "string") return fail("failed");

  // Whoever was signed in here before - possibly someone else, on a shared
  // computer - is signed out properly: their refresh token is revoked, not
  // merely replaced in the cookie, so it cannot be used again.
  const previous = await readSession();
  if (previous?.refresh && previous.refresh !== result.refresh) {
    await callUpstream({
      path: "/auth/logout/",
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: previous.refresh }),
      timeoutMs: 5_000,
    }).catch(() => undefined);
  }

  await writeSession({ access: result.access, refresh: result.refresh });
  // Signed in: a later visit may start a sign-in by itself again. Only the sign-out marker goes;
  // the loop guard expires on its own, so a browser that drops this session is still caught.
  await clearSignedOutMarker();
  if (typeof result.organization_id === "string") await writeOrganizationId(result.organization_id);

  // A new organization still needs its fiscal year: requireSession() sends
  // /dashboard there on its own (src/lib/auth/context.ts).
  return redirectToPath(kept.next ?? "/dashboard");
}

function parseFlowCookie(raw: string | undefined): { flow: string; next: string | null } | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as { flow?: unknown; next?: unknown };
    if (typeof value.flow !== "string" || !value.flow) return null;
    return { flow: value.flow, next: safePath(typeof value.next === "string" ? value.next : null) };
  } catch {
    return null;
  }
}

export const dynamic = "force-dynamic";
