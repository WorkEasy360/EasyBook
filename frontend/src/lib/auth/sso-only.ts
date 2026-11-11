import "server-only";

import { fetchSignInMethods, type SignInMethods } from "./workeasy360";

/**
 * Is EasyBook SSO-only (Workeasy360 is the only way in)?
 *
 * Django owns that decision (WORKEASY360_SSO_ONLY, answered by
 * /auth/methods/), so the frontend asks rather than keeping a second setting
 * that could disagree with it. The proxy needs the answer for every
 * navigation from a browser with no session (src/proxy.ts), so it is kept
 * in this process for SSO_ONLY_TTL_MS and shared by concurrent requests: the
 * backend is asked about once a minute, not once per visitor.
 *
 * Fail-safe: when the answer cannot be had, the last one stands; with none,
 * EasyBook is NOT treated as SSO-only, so a visitor is sent to the sign-in
 * page exactly as before. Not knowing never starts a sign-in by itself.
 */

export const SSO_ONLY_TTL_MS = 60_000;
/** After a failed question, the next one waits this long. */
export const SSO_ONLY_RETRY_MS = 10_000;
const SSO_ONLY_TIMEOUT_MS = 3_000;

/** Workeasy360 on and EasyBook's own password sign-in off. */
export function isSsoOnlyMethods(methods: SignInMethods): boolean {
  return methods.workeasy360 && !methods.password;
}

export function createSsoOnlyProbe(options: {
  ask: () => Promise<SignInMethods | null>;
  now?: () => number;
}): () => Promise<boolean> {
  const clock = options.now ?? Date.now;
  let known: { value: boolean; at: number } | null = null;
  let retryAt = 0;
  let asking: Promise<boolean> | null = null;

  async function refresh(): Promise<boolean> {
    let methods: SignInMethods | null;
    try {
      methods = await options.ask();
    } catch {
      methods = null;
    }
    const at = clock();
    if (methods) {
      known = { value: isSsoOnlyMethods(methods), at };
      retryAt = 0;
      return known.value;
    }
    retryAt = at + SSO_ONLY_RETRY_MS;
    return known?.value ?? false;
  }

  return async function isSsoOnly(): Promise<boolean> {
    const now = clock();
    if (known && now - known.at < SSO_ONLY_TTL_MS) return known.value;
    if (now < retryAt) return known?.value ?? false;
    asking ??= refresh().finally(() => {
      asking = null;
    });
    return asking;
  };
}

/** The process-wide probe the proxy uses. */
export const isSsoOnly = createSsoOnlyProbe({ ask: () => fetchSignInMethods({ timeoutMs: SSO_ONLY_TIMEOUT_MS }) });
