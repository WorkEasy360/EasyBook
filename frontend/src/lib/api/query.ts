/**
 * Query-string building, shared by the server and browser clients so a filter
 * serializes identically whichever side issues the request.
 */

export type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryValue | QueryValue[]>;

export function buildSearch(query: QueryParams | undefined): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const entry of value) {
        if (entry === undefined || entry === null || entry === "") continue;
        params.append(key, String(entry));
      }
    } else {
      params.set(key, String(value));
    }
  }
  return params.toString();
}
