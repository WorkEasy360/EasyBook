/**
 * Content Security Policy.
 *
 * Nonce-based, following the Next 16 guide bundled at
 * node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md:
 * the proxy mints a fresh nonce per request, Next reads it back out of the
 * request's CSP header and stamps it on every framework and page script, and
 * `'strict-dynamic'` lets those scripts load their own chunks. An injected
 * <script> without the nonce does not run — the point of a CSP in an app that
 * renders customer names, memos and OCR text supplied by other people.
 *
 * Decisions beyond the guide's example, each deliberate:
 *
 *  - `style-src-attr 'unsafe-inline'`. A nonce in style-src makes browsers
 *    IGNORE 'unsafe-inline' there, which would block every `style="…"`
 *    attribute — table column widths, and all of Recharts' SVG styling.
 *    CSP3 splits attributes out, so <style> ELEMENTS stay nonce-locked while
 *    attributes are allowed. A style attribute cannot execute script.
 *  - `connect-src 'self'`. The browser only ever talks to the same-origin BFF
 *    (frontend/CLAUDE.md). A connect-src naming Django would be a sign
 *    something bypassed it.
 *  - `frame-ancestors 'none'` duplicates X-Frame-Options for browsers that
 *    only honour one of the two.
 *  - Development adds `'unsafe-eval'` (React's dev error overlays use eval,
 *    per the guide), `ws:` for hot reload and inline <style> for HMR-injected
 *    CSS. None of these reach a production build.
 *  - `upgrade-insecure-requests` only for a request that came in over https
 *    (directly or via X-Forwarded-Proto). Behind plain http it upgrades the
 *    app's own requests to a scheme nothing is listening on.
 */

export function generateNonce(): string {
  // 128 bits from the platform CSPRNG, base64 — the guide's construction.
  return btoa(crypto.randomUUID());
}

export function buildContentSecurityPolicy(
  nonce: string,
  options: { development: boolean; https: boolean },
): string {
  const { development, https } = options;

  const directives: Array<[string, string[]]> = [
    ["default-src", ["'self'"]],
    ["script-src", ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'", ...(development ? ["'unsafe-eval'"] : [])]],
    // The dev server injects CSS through <style> elements it does not nonce
    // (HMR), so development allows inline styles. A nonce and 'unsafe-inline'
    // cannot be combined — browsers drop 'unsafe-inline' when a nonce is
    // present — hence the either/or. Production stays nonce-only.
    ["style-src", development ? ["'self'", "'unsafe-inline'"] : ["'self'", `'nonce-${nonce}'`]],
    ["style-src-attr", ["'unsafe-inline'"]],
    ["img-src", ["'self'", "blob:", "data:"]],
    ["font-src", ["'self'"]],
    ["connect-src", ["'self'", ...(development ? ["ws:"] : [])]],
    ["object-src", ["'none'"]],
    ["base-uri", ["'self'"]],
    ["form-action", ["'self'"]],
    ["frame-ancestors", ["'none'"]],
  ];

  const policy = directives.map(([name, values]) => `${name} ${values.join(" ")}`);
  // Only when the page itself arrived over https. On a plain-http origin it
  // rewrites every same-origin fetch and redirect to https and breaks them.
  if (!development && https) policy.push("upgrade-insecure-requests");
  return policy.join("; ");
}
