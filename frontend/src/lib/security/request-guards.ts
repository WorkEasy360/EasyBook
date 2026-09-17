/**
 * Guards for the same-origin route handlers (the BFF and /api/auth/*).
 *
 * The session lives in cookies, so these handlers are exactly what a
 * cross-site request forgery would target. SameSite=Lax already withholds the
 * cookies from a cross-site POST in current browsers; this is the second,
 * independent layer the root CLAUDE.md asks for ("fail closed"), and it also
 * covers a same-SITE sibling origin (another subdomain), which Lax does not.
 */

const STATE_CHANGING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * True when a browser tells us this state-changing request did not come from
 * our own origin.
 *
 * `Sec-Fetch-Site` is set by the browser and cannot be forged by page script;
 * only "same-origin" (our own pages) and "none" (typed/bookmarked) pass. When
 * it is absent — an older browser — the `Origin` header is compared with the
 * `Host` the request arrived on. A request carrying neither is not from a
 * browser at all (curl, a server-to-server call); forgery is a browser attack
 * and the cookies are still required, so it is let through.
 */
export function isForeignOriginWrite(method: string, headers: Headers): boolean {
  if (!STATE_CHANGING.has(method.toUpperCase())) return false;

  const fetchSite = headers.get("sec-fetch-site");
  if (fetchSite) return fetchSite !== "same-origin" && fetchSite !== "none";

  const origin = headers.get("origin");
  if (!origin) return false;
  if (origin === "null") return true;

  // Behind a reverse proxy the public host arrives as X-Forwarded-Host.
  const host = headers.get("x-forwarded-host") ?? headers.get("host");
  if (!host) return true;

  try {
    return new URL(origin).host !== host.split(",")[0]?.trim();
  } catch {
    return true;
  }
}

/** Largest body the BFF will buffer: the backend's largest upload (25 MiB PDF) plus multipart overhead. */
export const DEFAULT_MAX_BODY_BYTES = 26 * 1024 * 1024;

export class BodyTooLargeError extends Error {
  constructor(readonly limit: number) {
    super(`Request body exceeds ${limit} bytes.`);
    this.name = "BodyTooLargeError";
  }
}

/**
 * Reads a request body into memory, refusing to go past `limit`.
 *
 * `request.arrayBuffer()` would buffer whatever arrives. Content-Length is
 * checked first (cheap rejection), then the stream is counted as it is read,
 * because a chunked request has no length to trust.
 */
export async function readBodyWithLimit(request: Request, limit: number): Promise<ArrayBuffer | null> {
  const declared = request.headers.get("content-length");
  if (declared !== null && Number(declared) > limit) throw new BodyTooLargeError(limit);
  if (!request.body) return null;

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > limit) {
      await reader.cancel();
      throw new BodyTooLargeError(limit);
    }
    chunks.push(value);
  }

  const body = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body.buffer;
}
