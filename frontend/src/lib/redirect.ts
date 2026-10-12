import { NextResponse } from "next/server";

const BASE = "https://same-site.invalid";

/**
 * `target` when it is a path on this site, else null - the one check every
 * redirect target goes through (`next` after sign-in included).
 *
 * Browsers strip tabs and newlines from a URL and read a backslash as a slash,
 * so "/\t/evil.example" or "/\evil.example" would become "//evil.example":
 * another site. So no whitespace, control character or backslash at all, and
 * the browser's own reading of the path must stay on this site.
 */
export function sameSitePath(target: string | null | undefined): string | null {
  if (!target || !target.startsWith("/") || target.startsWith("//") || target.length > 2048) return null;
  for (const ch of target) {
    const code = ch.charCodeAt(0);
    if (code < 0x20 || code === 0x7f || ch === "\\" || /\s/.test(ch)) return null;
  }
  let resolved: URL;
  try {
    resolved = new URL(target, BASE);
  } catch {
    return null;
  }
  return resolved.origin === BASE ? target : null;
}

/**
 * A redirect to a path on this site, sent as a relative Location.
 *
 * Never `NextResponse.redirect(new URL(path, request.url))` in a route
 * handler: there Next builds `request.url` from the address this server
 * listens on, not from the address the browser used. Behind the production
 * proxy (Dockerfile: HOSTNAME=0.0.0.0) that is http://localhost:3000 whatever
 * Host or X-Forwarded-Host say, so the browser would be sent to its own
 * machine. A relative Location resolves against the address the browser
 * actually used. (src/proxy.ts is not affected: Next makes a same-origin
 * redirect from the proxy relative by itself.)
 *
 * Anything that is not a same-site path (sameSitePath) lands on the home page
 * rather than off-site.
 */
export function redirectToPath(path: string): NextResponse {
  return new NextResponse(null, { status: 307, headers: { Location: sameSitePath(path) ?? "/" } });
}
