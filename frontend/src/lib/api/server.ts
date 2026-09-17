import "server-only";

import { redirect } from "next/navigation";
import { callUpstream, decodeJson, throwIfError } from "./upstream";
import { DEFAULT_TIMEOUT_MS, REPORT_TIMEOUT_MS } from "./config";
import type { ApiError } from "./errors";
import { readSession } from "@/lib/auth/session";
import { maybeSession } from "@/lib/auth/context";
import type { Paginated } from "./types";
import { buildSearch, type QueryParams } from "./query";

/**
 * Server-side API client, for Server Components and Server Actions.
 *
 * A Server Component cannot set cookies, so this does NOT refresh on 401 —
 * it could get a new token but could not persist the rotated refresh token,
 * which would blacklist the stored one and break the session outright.
 * Refresh before render happens in src/middleware.ts, which can set cookies;
 * a 401 that still reaches here means the session is genuinely gone, and the
 * user is redirected to sign in.
 */

export interface ServerRequestOptions {
  /** Query parameters. Undefined and null entries are dropped. */
  query?: QueryParams;
  timeoutMs?: number;
  signal?: AbortSignal;
  /** Opt in to Next's data cache. Defaults to no-store (tenant data). */
  cache?: RequestCache;
  next?: { revalidate?: number | false; tags?: string[] };
}

async function request<T>(
  method: string,
  path: string,
  options: ServerRequestOptions & { body?: unknown; idempotencyKey?: string } = {},
): Promise<T> {
  const session = await readSession();
  if (!session) redirect("/login");

  // The organization cookie can be missing while the session is valid — the
  // organizations lookup at sign-in failed (throttled, say) or the cookie was
  // cleared. The page shell falls back to the first organization, so API calls
  // must use the SAME one. maybeSession(), NOT requireSession(): this client
  // also serves pages for a user with no organization yet (onboarding), and
  // requireSession() would redirect that user to the very page calling it — a
  // redirect loop. With no organization at all, no header is sent.
  const organizationId = session.organizationId ?? (await maybeSession())?.organization.id ?? null;

  const headers = new Headers({ Accept: "application/json" });
  headers.set("Authorization", `Bearer ${session.access}`);
  if (organizationId) headers.set("X-Organization-Id", organizationId);
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

  let body: BodyInit | null = null;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }

  const response = await callUpstream({
    path,
    method,
    search: buildSearch(options.query),
    headers,
    body,
    timeoutMs: options.timeoutMs ?? (path.startsWith("/reports/") ? REPORT_TIMEOUT_MS : DEFAULT_TIMEOUT_MS),
    ...(options.signal ? { signal: options.signal } : {}),
    cache: options.cache ?? "no-store",
    ...(options.next ? { next: options.next } : {}),
  });

  if (response.status === 401) {
    // Middleware already had its chance to refresh; the session is dead.
    redirect("/login?reason=expired");
  }

  throwIfError(response);
  return decodeJson(response) as T;
}

export const serverApi = {
  get<T>(path: string, options?: ServerRequestOptions): Promise<T> {
    return request<T>("GET", path, options ?? {});
  },
  list<T>(path: string, options?: ServerRequestOptions): Promise<Paginated<T>> {
    return request<Paginated<T>>("GET", path, options ?? {});
  },
  post<T>(path: string, body?: unknown, options?: ServerRequestOptions & { idempotencyKey?: string }): Promise<T> {
    return request<T>("POST", path, { ...(options ?? {}), body });
  },
  patch<T>(path: string, body?: unknown, options?: ServerRequestOptions): Promise<T> {
    return request<T>("PATCH", path, { ...(options ?? {}), body });
  },
  put<T>(path: string, body?: unknown, options?: ServerRequestOptions): Promise<T> {
    return request<T>("PUT", path, { ...(options ?? {}), body });
  },
  delete<T>(path: string, options?: ServerRequestOptions): Promise<T> {
    return request<T>("DELETE", path, options ?? {});
  },
};

/**
 * For a panel that should degrade to an inline error rather than fail the
 * whole page — the dashboard fans out across many report endpoints and one
 * slow or forbidden report must not blank the others (spec §17, §84).
 */
export async function tryServer<T>(
  load: () => Promise<T>,
): Promise<{ ok: true; data: T } | { ok: false; error: ApiError | Error }> {
  try {
    return { ok: true, data: await load() };
  } catch (error) {
    if (error instanceof Error && error.message === "NEXT_REDIRECT") throw error;
    // Next signals redirect()/notFound() by throwing; never swallow those.
    if (typeof error === "object" && error !== null && "digest" in error) throw error;
    return { ok: false, error: error instanceof Error ? error : new Error(String(error)) };
  }
}
