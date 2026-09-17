/**
 * Turning a download grant into something the browser can open.
 *
 * POST documents/{id}/download/ returns one of two shapes (live capture and
 * documents/storage/):
 *  - local storage (dev/test): a DJANGO path,
 *    "/api/v1/documents/local-storage/<signed-token>/". The browser cannot
 *    reach Django, so it is opened through the same-origin BFF instead. That
 *    view is AllowAny — the signed token is the credential — so the BFF adds
 *    nothing it relies on.
 *  - S3 (production): an absolute presigned https URL, opened as-is.
 *
 * Anything else is refused rather than navigated to. The URL is used once, on
 * click, and never stored or rendered into the page.
 */

const DJANGO_PREFIX = "/api/v1/";
const BFF_PREFIX = "/api/bff/";

export type GrantTarget = { kind: "same-origin"; href: string } | { kind: "external"; href: string };

export function grantTarget(url: string): GrantTarget | null {
  if (url.startsWith(DJANGO_PREFIX)) {
    const rest = url.slice(DJANGO_PREFIX.length);
    // Refuse anything that could climb out of the API prefix; the BFF rejects
    // it too, but a crafted grant should never even be requested.
    if (rest.split("/").some((segment) => segment === "." || segment === "..")) return null;
    // No trailing slash: Next redirects "/x/" to "/x" (308) before the BFF runs.
    return { kind: "same-origin", href: `${BFF_PREFIX}${rest.replace(/\/+$/, "")}` };
  }
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") return { kind: "external", href: parsed.toString() };
  } catch {
    return null;
  }
  return null;
}
