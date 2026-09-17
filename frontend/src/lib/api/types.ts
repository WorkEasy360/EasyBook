/**
 * Wire contracts shared by every endpoint.
 *
 * Mirrors backend/core/pagination.py and backend/core/exceptions.py. These
 * two shapes are the only ones guaranteed across all of /api/v1 — everything
 * else is per-resource and lives in src/types/api.
 */

/** backend/core/pagination.py :: DefaultPagination.get_paginated_response */
export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/**
 * DRF's `exc.get_codes()` returns a bare string for most errors but a nested
 * structure for ValidationError (field -> code[]), so this is deliberately
 * not `string`. Read it with `errorCodeOf()` rather than casting.
 */
export type ApiErrorCode = string | Record<string, unknown> | unknown[];

/** backend/core/exceptions.py :: api_exception_handler */
export interface ApiErrorEnvelope {
  error: {
    code: ApiErrorCode;
    message: string;
    /** DRF's raw `response.data` for validation errors; null for simple ones. */
    details: unknown;
    request_id: string;
  };
}

/** Field-keyed validation messages, the common `details` shape. */
export type FieldErrors = Record<string, string[]>;

/**
 * Money crosses the wire as a decimal STRING, never a number — DRF's
 * DecimalField serializes with COERCE_DECIMAL_TO_STRING (its default, and
 * not overridden in config/settings/base.py). Parsing one into a JS number
 * loses precision at scale, so the type keeps them apart on purpose.
 */
export type DecimalString = string;

/** ISO-8601 calendar date, e.g. "2026-03-31". */
export type DateString = string;

/** ISO-8601 instant with offset, e.g. "2026-03-31T09:15:00Z". */
export type DateTimeString = string;

export type UUID = string;

export const PAGE_SIZE_DEFAULT = 25;
/** backend/core/pagination.py :: DefaultPagination.max_page_size */
export const PAGE_SIZE_MAX = 200;
