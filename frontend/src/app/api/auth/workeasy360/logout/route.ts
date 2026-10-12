import { NextResponse } from "next/server";
import { callUpstream, decodeJson } from "@/lib/api/upstream";
import { redirectToPath } from "@/lib/redirect";
import { clientIpFromHeaders } from "@/lib/security/client-ip";

/**
 * "Sign out of EasyBook and Workeasy360" (RP-initiated logout, src/lib/auth/workeasy360.ts).
 *
 * The user menu ends EasyBook's own session first (POST /api/auth/logout, a
 * same-origin write); this GET only sends the browser on to Workeasy360's
 * end-session endpoint, which Django names from the provider's discovery
 * document with our client id and our registered post-logout URI. Workeasy360
 * asks the person before signing anyone out. Nothing here changes any state,
 * so a link to it cannot sign anyone out of EasyBook.
 */
export async function GET(request: Request): Promise<NextResponse> {
  let target = "";
  try {
    const upstream = await callUpstream({
      path: "/auth/workeasy360/end-session/",
      cache: "no-store",
      timeoutMs: 5_000,
      // The browser's own address, so the API's throttle is per person, not per BFF.
      clientIp: clientIpFromHeaders(request.headers),
    });
    const body = decodeJson(upstream) as { url?: unknown } | null;
    if (upstream.status === 200 && typeof body?.url === "string") target = body.url;
  } catch {
    target = "";
  }
  if (!/^https?:\/\//.test(target)) return redirectToPath("/login");
  return NextResponse.redirect(target);
}

export const dynamic = "force-dynamic";
