/**
 * The BFF's containment boundary: which browser-supplied paths may be
 * forwarded to Django with the user's bearer token attached.
 *
 * Next decodes each catch-all segment exactly once. What arrives here is
 * therefore judged as-is and NEVER decoded again or normalized: a segment is
 * forwarded only if it is made entirely of URL-unreserved characters and
 * starts with a letter or digit. That single rule rejects
 *
 *   ".." / "."                  (must start with a letter or digit)
 *   ".%2e", "%2e%2e", "%252e"   (no "%": a still-encoded dot would be decoded
 *                                later by URL parsing and become "..")
 *   "a\\b", "a/b", "%2f"        (no separators of any kind)
 *   spaces, quotes, NULs, ";", "?" and non-ASCII
 *
 * Every real segment the app uses is a route word ("fiscal-years") or a UUID,
 * both of which pass. The upstream helper independently re-checks that the
 * final URL is exactly inside the API base (src/lib/api/upstream.ts), so a
 * future loosening here still cannot reach /admin or any non-API path.
 */
const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._~-]*$/;

/** The Django path for these segments, or null if any segment is unsafe. */
export function resolveBffPath(segments: readonly string[]): string | null {
  if (segments.length === 0) return null;
  for (const segment of segments) {
    if (!SAFE_SEGMENT.test(segment)) return null;
  }
  // Django's URLconf declares every route with a trailing slash and
  // APPEND_SLASH cannot fix a POST, so the slash is added here rather than
  // relied upon from the caller.
  return `/${segments.join("/")}/`;
}

/**
 * Builds the upstream URL and proves it stays inside the API base: same
 * origin, and a pathname that is exactly base + path after the URL parser's
 * own normalization (dot-segment removal, percent-encoding). Any difference
 * means the path would have been rewritten on the way out, so it is refused.
 */
export function containedUpstreamUrl(apiBase: string, path: string, search?: string): string {
  const base = new URL(apiBase);
  const basePath = base.pathname.replace(/\/+$/, "");
  const leading = path.startsWith("/") ? path : `/${path}`;
  const normalized = leading.endsWith("/") ? leading : `${leading}/`;
  const expectedPath = `${basePath}${normalized}`;

  const url = new URL(`${base.origin}${expectedPath}`);
  if (url.origin !== base.origin || url.pathname !== expectedPath || normalized.includes("//")) {
    throw new Error("Refusing an upstream path that escapes the API base.");
  }
  return search ? `${url.origin}${url.pathname}?${search}` : `${url.origin}${url.pathname}`;
}
