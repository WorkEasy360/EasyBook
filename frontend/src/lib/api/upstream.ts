import "server-only";

import { forwardedClientHeaders } from "@/lib/security/client-ip";
import { containedUpstreamUrl } from "./bff-path";
import { NetworkError, TimeoutError, toApiError } from "./errors";
import { DEFAULT_TIMEOUT_MS, apiBaseUrl } from "./config";

/**
 * The one place this application issues a request to Django. Everything above
 * it (the server client, the BFF proxy, the auth routes) goes through here so
 * that timeouts, request-id propagation and error normalization cannot drift
 * apart between call sites.
 */

export interface UpstreamRequest {
  path: string;
  method?: string;
  /** Already-encoded query string, without the leading "?". */
  search?: string;
  body?: BodyInit | null;
  headers?: HeadersInit;
  timeoutMs?: number;
  signal?: AbortSignal;
  /**
   * The browser's address, for Django's rate limiting only (Django sees this
   * server otherwise — src/lib/security/client-ip.ts).
   */
  clientIp?: string | null;
  /** Next.js fetch cache directives, for Server Component reads. */
  cache?: RequestCache;
  next?: { revalidate?: number | false; tags?: string[] };
}

export interface UpstreamResponse {
  status: number;
  headers: Headers;
  body: ArrayBuffer;
  requestId: string | null;
}

/**
 * Every route in backend/config/urls.py is declared WITH a trailing slash, and
 * Django's APPEND_SLASH redirect cannot rescue a POST (it would turn it into a
 * GET). Normalising here rather than at each call site means a missing slash
 * cannot silently 404 one transport while the other works.
 */
function buildUrl(path: string, search?: string): string {
  // Throws if the path would leave the API base once parsed (dot segments,
  // encoded dots, another origin) — the second, independent containment
  // check behind the BFF's own segment allowlist (./bff-path.ts).
  return containedUpstreamUrl(apiBaseUrl(), path, search);
}

export async function callUpstream(request: UpstreamRequest): Promise<UpstreamResponse> {
  const timeoutMs = request.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // Honour a caller-supplied signal (a cancelled navigation) as well as the
  // timeout, without losing the ability to tell the two apart afterwards.
  if (request.signal) {
    if (request.signal.aborted) controller.abort();
    else request.signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  const headers = new Headers(request.headers);
  for (const [name, value] of Object.entries(forwardedClientHeaders(request.clientIp))) {
    headers.set(name, value);
  }

  try {
    const response = await fetch(buildUrl(request.path, request.search), {
      method: request.method ?? "GET",
      headers,
      body: request.body ?? null,
      signal: controller.signal,
      redirect: "manual",
      cache: request.cache,
      ...(request.next ? { next: request.next } : {}),
    });

    return {
      status: response.status,
      headers: response.headers,
      body: await response.arrayBuffer(),
      requestId: response.headers.get("X-Request-ID"),
    };
  } catch (error) {
    if (controller.signal.aborted && !request.signal?.aborted) {
      throw new TimeoutError(timeoutMs);
    }
    throw new NetworkError("Could not reach EasyBook. Check your connection and try again.", error);
  } finally {
    clearTimeout(timer);
  }
}

/** Decodes an upstream body as JSON, tolerating an empty 204. */
export function decodeJson(response: UpstreamResponse): unknown {
  if (response.body.byteLength === 0) return null;
  const text = new TextDecoder().decode(response.body);
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

/** Throws the normalized ApiError for any non-2xx upstream response. */
export function throwIfError(response: UpstreamResponse): void {
  if (response.status >= 200 && response.status < 300) return;
  throw toApiError(response.status, decodeJson(response), response.requestId);
}
