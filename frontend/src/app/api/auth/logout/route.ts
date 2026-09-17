import { NextResponse } from "next/server";
import { isForeignOriginWrite } from "@/lib/security/request-guards";
import { clearSession, readSession } from "@/lib/auth/session";
import { callUpstream } from "@/lib/api/upstream";

/**
 * Ends the session. The cookies are cleared unconditionally — a failed
 * upstream call must never leave the user apparently signed in.
 */
export async function POST(request: Request): Promise<NextResponse> {
  // A cross-site page must not be able to sign the user out.
  if (isForeignOriginWrite(request.method, request.headers)) {
    return NextResponse.json(
      {
        error: {
          code: "cross_origin_request",
          message: "This request did not come from EasyBook.",
          details: null,
          request_id: crypto.randomUUID(),
        },
      },
      { status: 403 },
    );
  }

  const session = await readSession();

  if (session) {
    // Best-effort server-side invalidation. backend/accounts/urls.py exposes
    // no /auth/logout/ route, but SimpleJWT is configured with
    // ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION, so refreshing once
    // and discarding the result blacklists the token we are about to drop —
    // the closest available equivalent to a real logout.
    await callUpstream({
      path: "/auth/refresh/",
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: session.refresh }),
      timeoutMs: 5_000,
    }).catch(() => undefined);
  }

  await clearSession();
  return NextResponse.json({ ok: true });
}

export const dynamic = "force-dynamic";
