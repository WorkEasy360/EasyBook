"use client";

import { ApiError, NetworkError, TimeoutError, toApiError } from "./errors";
import type { Paginated } from "./types";
import { buildSearch, type QueryParams } from "./query";

/**
 * Browser-side API client. Talks to the same-origin BFF (src/app/api/bff),
 * never to Django — so there is no base URL, no CORS, and above all no token
 * handling here. The cookie rides along automatically and the proxy attaches
 * the Authorization and X-Organization-Id headers server-side.
 *
 * There is exactly one of these (spec §13). Features must not write their own
 * fetch wrappers.
 */

const BFF_PREFIX = "/api/bff";
const DEFAULT_TIMEOUT_MS = 30_000;

export interface ClientRequestOptions {
  query?: QueryParams;
  signal?: AbortSignal;
  timeoutMs?: number;
  /**
   * Sent as Idempotency-Key so a retried critical write cannot post twice
   * (backend/core/idempotency.py, spec §73). Generate one per SUBMISSION,
   * not per attempt — see newIdempotencyKey().
   */
  idempotencyKey?: string;
}

/**
 * No trailing slash. Next redirects `/api/bff/x/` to `/api/bff/x` with a 308,
 * so a slash here cost a round trip on every call — and under the production
 * CSP's `upgrade-insecure-requests` that redirect was upgraded to https and
 * failed outright on a plain-http origin. Django's required slash is added by
 * the BFF itself (route.ts :: resolvePath).
 */
function normalizePath(path: string): string {
  const trimmed = path.replace(/^\/+/, "").replace(/\/+$/, "");
  return `${BFF_PREFIX}/${trimmed}`;
}

async function request<T>(
  method: string,
  path: string,
  options: ClientRequestOptions & { body?: unknown } = {},
): Promise<T> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  if (options.signal) {
    if (options.signal.aborted) controller.abort();
    else options.signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  const search = buildSearch(options.query);
  const headers = new Headers({ Accept: "application/json" });
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

  let body: BodyInit | null = null;
  if (options.body instanceof FormData) {
    // Let the browser set the multipart boundary itself.
    body = options.body;
  } else if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(`${normalizePath(path)}${search ? `?${search}` : ""}`, {
      method,
      headers,
      body,
      signal: controller.signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (controller.signal.aborted && !options.signal?.aborted) throw new TimeoutError(timeoutMs);
    throw new NetworkError("Could not reach EasyBook. Check your connection and try again.", error);
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 204) return null as T;

  const requestId = response.headers.get("X-Request-ID");
  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text().catch(() => null);

  if (!response.ok) {
    const error = toApiError(response.status, payload, requestId);
    // A 401 here means the BFF could not refresh either: the session is over.
    // A full navigation (not router.push) guarantees every cached Server
    // Component payload for the old session is dropped.
    if (error.status === 401 && typeof window !== "undefined") {
      // A HARD navigation is the point: router.push would keep the router
      // cache and every Server Component payload rendered for the dead session.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/login?reason=expired");
    }
    throw error;
  }

  return payload as T;
}

export const api = {
  get<T>(path: string, options?: ClientRequestOptions): Promise<T> {
    return request<T>("GET", path, options ?? {});
  },
  list<T>(path: string, options?: ClientRequestOptions): Promise<Paginated<T>> {
    return request<Paginated<T>>("GET", path, options ?? {});
  },
  post<T>(path: string, body?: unknown, options?: ClientRequestOptions): Promise<T> {
    return request<T>("POST", path, { ...(options ?? {}), body });
  },
  patch<T>(path: string, body?: unknown, options?: ClientRequestOptions): Promise<T> {
    return request<T>("PATCH", path, { ...(options ?? {}), body });
  },
  put<T>(path: string, body?: unknown, options?: ClientRequestOptions): Promise<T> {
    return request<T>("PUT", path, { ...(options ?? {}), body });
  },
  delete<T>(path: string, options?: ClientRequestOptions): Promise<T> {
    return request<T>("DELETE", path, options ?? {});
  },
};

export { ApiError };
