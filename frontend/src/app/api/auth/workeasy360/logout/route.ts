import { NextResponse } from "next/server";
import { callUpstream, decodeJson } from "@/lib/api/upstream";
import { markSignedOut } from "@/lib/auth/session";
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
 *
 * It leaves the sign-out marker (src/lib/auth/direct-visit.ts) again - the POST
 * normally has already - but only for a request that came from EasyBook's own
 * pages (`Sec-Fetch-Site: same-origin`, or no such header at all, as an older
 * browser or a server-side call sends). A link on another site, another
 * subdomain included, must not be able to set it. The marker only stops a
 * visit with no session from starting a sign-in by itself; the next sign-in
 * clears it. So when Workeasy360 returns the browser to EasyBook, it is shown
 * the sign-in page - never signed straight back in.
 */
export async function GET(request: Request): Promise<NextResponse> {
  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite === null || fetchSite === "same-origin") await markSignedOut();
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
