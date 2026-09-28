/**
 * Configuration a production frontend must have before it serves anyone.
 * Checked once at server start (src/instrumentation.ts); a failure stops the
 * process, because every one of these silently weakens security otherwise:
 *
 * - SESSION_COOKIE_SECURE=1: without it the session cookies (the access and
 *   refresh tokens) are sent over plain HTTP too.
 * - BFF_PROXY_SECRET: without it the API cannot trust the browser address the
 *   BFF forwards, so every user shares one sign-in rate-limit bucket
 *   (backend/core/client_ip.py).
 * - API_BASE_URL: an absolute http(s) URL whose path is the API prefix; the
 *   dev default (127.0.0.1:8001) must never be what production talks to.
 * - TRUSTED_PROXY_COUNT: a non-negative integer (reverse proxies in front of
 *   this server that append to X-Forwarded-For).
 */
export function productionConfigProblems(env: Record<string, string | undefined>): string[] {
  const problems: string[] = [];

  if (env.SESSION_COOKIE_SECURE !== "1") {
    problems.push("SESSION_COOKIE_SECURE must be 1 (session cookies must be Secure).");
  }

  const secret = env.BFF_PROXY_SECRET ?? "";
  if (secret.length < 32) {
    problems.push("BFF_PROXY_SECRET must be set to the API's shared secret (at least 32 characters).");
  }

  const base = env.API_BASE_URL ?? "";
  let parsed: URL | null = null;
  try {
    parsed = new URL(base);
  } catch {
    parsed = null;
  }
  if (!parsed || (parsed.protocol !== "https:" && parsed.protocol !== "http:")) {
    problems.push("API_BASE_URL must be an absolute http(s) URL, e.g. https://app.example.com/api/v1.");
  } else if (!parsed.pathname.replace(/\/+$/, "").endsWith("/api/v1")) {
    problems.push("API_BASE_URL must end with the API prefix /api/v1.");
  } else if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    problems.push("API_BASE_URL must not carry credentials, a query or a fragment.");
  }

  const proxies = env.TRUSTED_PROXY_COUNT ?? "";
  if (!/^\d+$/.test(proxies)) {
    problems.push("TRUSTED_PROXY_COUNT must be set to the number of reverse proxies in front of this server (0 or more).");
  }

  return problems;
}
