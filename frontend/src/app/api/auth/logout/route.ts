import { NextResponse } from "next/server";
import { isForeignOriginWrite } from "@/lib/security/request-guards";
import { clearSession, markSignedOut, readSession } from "@/lib/auth/session";
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
    // Server-side revocation: POST /auth/logout/ blacklists the refresh token
    // (accounts.views.LogoutView). It always answers 204, so there is no
    // outcome to branch on; a network failure still falls through to
    // clearing the cookies below.
    await callUpstream({
      path: "/auth/logout/",
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: session.refresh }),
      timeoutMs: 5_000,
    }).catch(() => undefined);
  }

  await clearSession();
  // Leave the sign-out marker: until the next sign-in this browser is shown
  // the sign-in page, never sent to Workeasy360 by itself (direct-visit.ts).
  // Both sign-outs go through here first, "...and Workeasy360" included.
  await markSignedOut();
  return NextResponse.json({ ok: true });
}

export const dynamic = "force-dynamic";
