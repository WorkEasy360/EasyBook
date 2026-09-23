import "server-only";

/**
 * Server-side configuration. Nothing here is NEXT_PUBLIC_*: if the Django
 * base URL were public, feature code could call it from the browser and would
 * need a token in JS to do so, undoing the whole session design.
 */

export function apiBaseUrl(): string {
  const raw = process.env.API_BASE_URL ?? "http://127.0.0.1:8001/api/v1";
  return raw.replace(/\/+$/, "");
}

/** Default per-request deadline. Long reports override it explicitly. */
export const DEFAULT_TIMEOUT_MS = 30_000;
/** Report and export endpoints legitimately take longer than a CRUD call. */
export const REPORT_TIMEOUT_MS = 90_000;
