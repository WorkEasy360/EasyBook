"use client";

import * as React from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";
import { api } from "@/lib/api/browser";
import type { QueryParams } from "@/lib/api/query";
import type { Paginated } from "@/lib/api/types";
import type { ApiError } from "@/lib/api/errors";
import { invalidationTargets, queryKeys } from "@/lib/api/query-keys";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import { useOrg } from "@/components/providers/org-provider";

/**
 * Data hooks. Every feature reads and writes through these, so tenant scoping,
 * cache invalidation and idempotency cannot be forgotten at a call site.
 */

type QueryTuning<T> = Omit<UseQueryOptions<T, ApiError>, "queryKey" | "queryFn">;

/** Paginated list. Server does the paging, sorting and filtering. */
export function useList<T>(
  resource: string,
  params?: QueryParams,
  options?: QueryTuning<Paginated<T>>,
): UseQueryResult<Paginated<T>, ApiError> {
  const { organizationId } = useOrg();
  return useQuery<Paginated<T>, ApiError>({
    queryKey: queryKeys.list(organizationId, resource, params),
    queryFn: () => api.list<T>(resource, { query: params }),
    ...options,
  });
}

/** One record. `id` may be undefined while a route param resolves. */
export function useDetail<T>(
  resource: string,
  id: string | undefined,
  options?: QueryTuning<T>,
): UseQueryResult<T, ApiError> {
  const { organizationId } = useOrg();
  return useQuery<T, ApiError>({
    queryKey: queryKeys.detail(organizationId, resource, id ?? ""),
    queryFn: () => api.get<T>(`${resource}/${id}`),
    enabled: Boolean(id) && (options?.enabled ?? true),
    ...options,
  });
}

/** A sub-collection of one record, e.g. an invoice's payment allocations. */
export function useNested<T>(
  resource: string,
  id: string | undefined,
  child: string,
  params?: QueryParams,
  options?: QueryTuning<T>,
): UseQueryResult<T, ApiError> {
  const { organizationId } = useOrg();
  return useQuery<T, ApiError>({
    queryKey: queryKeys.nested(organizationId, resource, id ?? "", child, params),
    queryFn: () => api.get<T>(`${resource}/${id}/${child}`, { query: params }),
    enabled: Boolean(id) && (options?.enabled ?? true),
    ...options,
  });
}

/** A report. Longer stale time — a P&L does not change between two clicks. */
export function useReport<T>(
  path: string,
  params?: QueryParams,
  options?: QueryTuning<T>,
): UseQueryResult<T, ApiError> {
  const { organizationId } = useOrg();
  return useQuery<T, ApiError>({
    queryKey: queryKeys.report(organizationId, path, params),
    queryFn: () => api.get<T>(`reports/${path}`, { query: params, timeoutMs: 90_000 }),
    staleTime: 60_000,
    ...options,
  });
}

/**
 * Invalidates a resource and everything its write cascades into
 * (src/lib/api/query-keys.ts :: INVALIDATES_ON_WRITE).
 */
export function useInvalidate() {
  const client = useQueryClient();
  const { organizationId } = useOrg();

  return React.useCallback(
    (resource: string) => {
      for (const target of invalidationTargets(resource)) {
        void client.invalidateQueries({
          queryKey: queryKeys.resource(organizationId, target),
          // Prefix match: expires the lists, the details and the nested
          // collections of that resource in one call.
          exact: false,
        });
      }
    },
    [client, organizationId],
  );
}

export interface MutationOptions<TData, TVariables> {
  onSuccess?: (data: TData, variables: TVariables) => void;
  onError?: (error: ApiError, variables: TVariables) => void;
  /** Resource whose caches this write expires. Defaults to `resource`. */
  invalidates?: string;
}

/**
 * A write that MUST NOT happen twice.
 *
 * The idempotency key is minted once per submission and reused across retries
 * of that submission, which is the only way the backend's replay protection
 * works (backend/core/idempotency.py). Regenerating it per attempt would make
 * a retried "Post Invoice" post a second invoice.
 *
 * The key is cleared after a definitive success, so the next deliberate
 * submission is genuinely new.
 */
export function useIdempotentMutation<TData, TVariables = void>(
  resource: string,
  request: (variables: TVariables, idempotencyKey: string) => Promise<TData>,
  options: MutationOptions<TData, TVariables> = {},
): UseMutationResult<TData, ApiError, TVariables> {
  const invalidate = useInvalidate();
  const keyRef = React.useRef<string | null>(null);

  return useMutation<TData, ApiError, TVariables>({
    mutationFn: (variables) => {
      keyRef.current ??= newIdempotencyKey();
      return request(variables, keyRef.current);
    },
    onSuccess: (data, variables) => {
      keyRef.current = null;
      invalidate(options.invalidates ?? resource);
      options.onSuccess?.(data, variables);
    },
    onError: (error, variables) => {
      // Keep the key on a retryable failure so a retry replays rather than
      // duplicates. Drop it when the request was rejected on its merits —
      // the user will edit and submit something genuinely different.
      if (error.isValidation || error.isForbidden) keyRef.current = null;
      options.onError?.(error, variables);
    },
  });
}

/** An ordinary write with no replay risk — a draft edit, a filter save. */
export function useApiMutation<TData, TVariables = void>(
  resource: string,
  request: (variables: TVariables) => Promise<TData>,
  options: MutationOptions<TData, TVariables> = {},
): UseMutationResult<TData, ApiError, TVariables> {
  const invalidate = useInvalidate();

  return useMutation<TData, ApiError, TVariables>({
    mutationFn: request,
    onSuccess: (data, variables) => {
      invalidate(options.invalidates ?? resource);
      options.onSuccess?.(data, variables);
    },
    onError: options.onError,
  });
}

/**
 * A state-transition action: post, void, issue, confirm, approve.
 *
 * Always idempotent — every one of these moves the ledger or a legal document
 * state (spec §73).
 */
export function useAction<TData = unknown>(
  resource: string,
  options: MutationOptions<TData, { id: string; action: string; body?: unknown }> = {},
) {
  return useIdempotentMutation<TData, { id: string; action: string; body?: unknown }>(
    resource,
    ({ id, action, body }, idempotencyKey) =>
      api.post<TData>(`${resource}/${id}/${action}`, body ?? {}, { idempotencyKey }),
    options,
  );
}

/** Typeahead lookup for a picker. Short keys are not sent — they match everything. */
export function useLookup<T>(
  resource: string,
  kind: string,
  search: string,
  options?: { enabled?: boolean; extraParams?: QueryParams },
): UseQueryResult<Paginated<T>, ApiError> {
  const { organizationId } = useOrg();
  return useQuery<Paginated<T>, ApiError>({
    // Keyed under the RESOURCE, not a separate "lookup" namespace: creating a
    // customer invalidates "sales/customers" by prefix, and a picker cached
    // under some other root would keep offering the list without it.
    queryKey: [...queryKeys.resource(organizationId, resource), "lookup", kind, search, options?.extraParams ?? {}],
    queryFn: () =>
      api.list<T>(resource, {
        query: { search: search || undefined, page_size: 20, ...options?.extraParams },
      }),
    enabled: options?.enabled ?? true,
    // Reference data changes rarely; a picker should not refetch per keystroke
    // once a term has been seen.
    staleTime: 120_000,
    placeholderData: (previous) => previous,
  });
}
