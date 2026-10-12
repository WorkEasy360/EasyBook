import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { callUpstream, decodeJson } from "@/lib/api/upstream";
import { cookieSecurity } from "@/lib/auth/session";
import {
  SSO_FLOW_COOKIE,
  SSO_FLOW_MAX_AGE_SECONDS,
  SSO_FLOW_PATH,
  organizationParam,
  safePath,
} from "@/lib/auth/workeasy360";
import { redirectToPath } from "@/lib/redirect";
import { clientIpFromHeaders } from "@/lib/security/client-ip";

/**
 * Sends the browser to Workeasy360 to sign in (src/lib/auth/workeasy360.ts).
 *
 * A GET, because it is reached by navigation: the login page's button, and
 * the Workeasy360 launcher (`/?organization=<uuid>`, redirected here by
 * src/proxy.ts). Starting a sign-in changes nothing until the callback, and a
 * sign-in a cross-site page forced would only sign the user in as themselves,
 * so this needs no cross-origin guard; the callback is what is protected, by
 * state, nonce and PKCE.
 */
interface StartResponse {
  authorization_url?: unknown;
  flow?: unknown;
}

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const organization = organizationParam(url.searchParams.get("organization"));
  const next = safePath(url.searchParams.get("next"));

  let started: StartResponse | null = null;
  try {
    const upstream = await callUpstream({
      path: "/auth/workeasy360/start/",
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ organization }),
      clientIp: clientIpFromHeaders(request.headers),
    });
    if (upstream.status === 200) started = decodeJson(upstream) as StartResponse | null;
  } catch {
    started = null;
  }

  const authorizationUrl = typeof started?.authorization_url === "string" ? started.authorization_url : "";
  const flow = typeof started?.flow === "string" ? started.flow : "";
  if (!flow || !/^https?:\/\//.test(authorizationUrl)) {
    return redirectToPath("/login?sso_error=unavailable");
  }

  const store = await cookies();
  store.set(SSO_FLOW_COOKIE, JSON.stringify({ flow, next }), {
    ...cookieSecurity(),
    path: SSO_FLOW_PATH,
    maxAge: SSO_FLOW_MAX_AGE_SECONDS,
  });
  return NextResponse.redirect(authorizationUrl);
}

export const dynamic = "force-dynamic";
