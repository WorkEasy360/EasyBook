import { capabilitiesFor } from "./api/capabilities";
import type { QueryParams } from "./api/query";
import type { Paginated } from "./api/types";

/**
 * Server-side list state.
 *
 * Reads the URL's search params, keeps only what the endpoint actually
 * supports (src/lib/api/capabilities.ts) and produces both the API query and
 * the hrefs the pagination links need. Filters live in the URL so a filtered
 * list survives a refresh and can be shared as a link (spec §66).
 *
 * Dropping unknown params is not tidiness: it stops an unrelated query string
 * (a `?focus=` UI hint, a campaign tag) being forwarded to DRF as a filter.
 */

export const DEFAULT_PAGE_SIZE = 25;
const MAX_PAGE_SIZE = 200;

/** What Next gives a page for `searchParams`. */
export type RawSearchParams = Record<string, string | string[] | undefined>;

export interface ListQuery {
  page: number;
  pageSize: number;
  /** Only the filters this endpoint understands. */
  filters: Record<string, string>;
  /** Exactly what goes on the wire. */
  apiParams: QueryParams;
  activeFilterCount: number;
  /** Href for a page number, preserving every active filter. */
  buildPageHref: (page: number) => string;
  /** Href with one filter changed — used by the filter controls. */
  buildFilterHref: (key: string, value: string | null) => string;
  /** Href with every filter cleared. */
  clearHref: string;
}

function firstValue(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value[0];
  return value;
}

export function parseListQuery(
  pathname: string,
  resource: string,
  searchParams: RawSearchParams,
): ListQuery {
  const capabilities = capabilitiesFor(resource);

  const filters: Record<string, string> = {};
  for (const key of capabilities.filters) {
    const value = firstValue(searchParams[key]);
    if (value !== undefined && value !== "") filters[key] = value;
  }

  const rawPage = Number(firstValue(searchParams["page"]) ?? "1");
  const rawSize = Number(firstValue(searchParams["page_size"]) ?? String(DEFAULT_PAGE_SIZE));

  // Guard the numerics: ?page=abc or ?page=-3 must never reach the API.
  const page = Number.isFinite(rawPage) && rawPage >= 1 ? Math.floor(rawPage) : 1;
  const pageSize =
    Number.isFinite(rawSize) && rawSize >= 1 && rawSize <= MAX_PAGE_SIZE
      ? Math.floor(rawSize)
      : DEFAULT_PAGE_SIZE;

  function href(overrides: Record<string, string | number | null>): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) params.set(key, value);
    if (pageSize !== DEFAULT_PAGE_SIZE) params.set("page_size", String(pageSize));
    if (page > 1) params.set("page", String(page));

    for (const [key, value] of Object.entries(overrides)) {
      if (value === null || value === "") params.delete(key);
      else params.set(key, String(value));
    }

    const query = params.toString();
    return query ? `${pathname}?${query}` : pathname;
  }

  return {
    page,
    pageSize,
    filters,
    apiParams: {
      page: page > 1 ? page : undefined,
      page_size: pageSize !== DEFAULT_PAGE_SIZE ? pageSize : undefined,
      ...filters,
    },
    activeFilterCount: Object.keys(filters).length,
    buildPageHref: (next) => href({ page: next <= 1 ? null : next }),
    // Changing a filter always returns to page 1: being left on page 7 of a
    // newly filtered list that has two pages shows an empty table and looks
    // broken.
    buildFilterHref: (key, value) => href({ [key]: value, page: null }),
    clearHref: pathname,
  };
}

/**
 * Wraps an unpaginated result (a report's `rows`, a bare array) in the page
 * shape DataTable renders. One page holding everything, so no pager appears.
 */
export function wholeList<T>(rows: readonly T[] | null | undefined): Paginated<T> {
  const results = rows ? [...rows] : [];
  return { count: results.length, next: null, previous: null, results };
}

/** Reads one string param, dropping arrays and empties. */
export function paramOf(searchParams: RawSearchParams, key: string): string | undefined {
  const value = searchParams[key];
  const first = Array.isArray(value) ? value[0] : value;
  return first === undefined || first === "" ? undefined : first;
}

/** A YYYY-MM-DD param, or undefined — a malformed date never reaches the API. */
export function dateParamOf(searchParams: RawSearchParams, key: string): string | undefined {
  const value = paramOf(searchParams, key);
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : undefined;
}
