/**
 * Forwarding the browser's address to Django — for rate limiting only.
 *
 * Django never sees the browser: every call arrives from this server. Without
 * the browser's address, Django's anonymous and sign-in rate limits treated
 * every user as ONE client, so a handful of failed sign-ins anywhere throttled
 * sign-in (and token refresh) for everyone. backend/core/client_ip.py trusts
 * the forwarded address only alongside BFF_PROXY_SECRET, so a client cannot
 * forge it by setting the header itself.
 *
 * Which address is the browser's: X-Forwarded-For counted TRUSTED_PROXY_COUNT
 * entries from the right. Each reverse proxy in front of this server appends
 * the address that connected to it; entries to the left of those were written
 * by the client and are never used. With no trusted proxy (local development)
 * there is no trustworthy address to forward, and nothing is sent.
 */

export const CLIENT_IP_HEADER = "X-EasyBook-Client-IP";
export const PROXY_AUTH_HEADER = "X-EasyBook-Proxy-Auth";

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const IPV6 = /^[0-9a-f]{0,4}(:[0-9a-f]{0,4}){2,7}$/i;

function isIpAddress(value: string): boolean {
  return IPV4.test(value) || (value.includes(":") && IPV6.test(value));
}

function trustedProxyCount(): number {
  const configured = Number(process.env.TRUSTED_PROXY_COUNT ?? "0");
  return Number.isInteger(configured) && configured > 0 ? configured : 0;
}

/** The browser's address as recorded by the trusted proxies, or null. */
export function clientIpFromHeaders(headers: Headers, trusted: number = trustedProxyCount()): string | null {
  if (trusted <= 0) return null;
  const forwardedFor = headers.get("x-forwarded-for");
  if (!forwardedFor) return null;
  const entries = forwardedFor
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  if (entries.length === 0) return null;
  const candidate = entries[entries.length - Math.min(trusted, entries.length)] ?? "";
  return isIpAddress(candidate) ? candidate : null;
}

/**
 * Headers asserting the browser's address to Django. Empty unless both an
 * address and the shared secret are available — an address without the secret
 * would be ignored by Django anyway.
 */
export function forwardedClientHeaders(clientIp: string | null | undefined): Record<string, string> {
  const secret = process.env.BFF_PROXY_SECRET;
  if (!clientIp || !secret) return {};
  return { [CLIENT_IP_HEADER]: clientIp, [PROXY_AUTH_HEADER]: secret };
}
