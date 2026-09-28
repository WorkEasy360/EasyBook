import "server-only";

import { serverApi, tryServer } from "./server";

/**
 * Resolving ids to records for display.
 *
 * Every read serializer returns relations as bare ids — an invoice carries
 * `customer: "5a5a…"`, not a name — so a page that shows names has to look
 * them up. Two strategies, chosen per call site:
 *
 *  - `recordsById` fetches each DISTINCT id directly. Exact, and right for a
 *    detail page with a handful of references.
 *  - `indexList` loads one page of a list and indexes it. One request, right
 *    for a list page with many rows sharing few parties — and `recordsById`
 *    backfills anything the page did not contain, so a name is never silently
 *    replaced by a raw id just because the organization has more than 200
 *    customers.
 *
 * Failures (a deleted record, a 403 for a role without that permission) leave
 * the id out of the map; callers render a neutral fallback.
 */

export async function recordsById<T extends { id: string }>(
  resource: string,
  ids: Iterable<string | null | undefined>,
): Promise<Map<string, T>> {
  const unique = [...new Set([...ids].filter((id): id is string => Boolean(id)))];
  const results = await Promise.all(
    unique.map((id) => tryServer(() => serverApi.get<T>(`${resource}/${id}`))),
  );

  const map = new Map<string, T>();
  for (const result of results) {
    if (result.ok) map.set(result.data.id, result.data);
  }
  return map;
}

export async function indexList<T extends { id: string }>(
  resource: string,
  ids: Iterable<string | null | undefined>,
  query: Record<string, string | number | undefined> = {},
): Promise<Map<string, T>> {
  const wanted = [...new Set([...ids].filter((id): id is string => Boolean(id)))];
  if (wanted.length === 0) return new Map();

  const page = await tryServer(() => serverApi.list<T>(resource, { query: { page_size: 200, ...query } }));
  const map = new Map<string, T>();
  if (page.ok) {
    for (const row of page.data.results) map.set(row.id, row);
  }

  const missing = wanted.filter((id) => !map.has(id));
  if (missing.length > 0) {
    for (const [id, record] of await recordsById<T>(resource, missing)) map.set(id, record);
  }
  return map;
}

/** "1100 · Accounts Receivable", or a neutral fallback. */
export function accountLabel(
  accounts: Map<string, { code: string; name: string }>,
  id: string | null | undefined,
  fallback = "—",
): string {
  if (!id) return fallback;
  const account = accounts.get(id);
  return account ? `${account.code} · ${account.name}` : "Configured";
}
