"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/errors";

/**
 * TanStack Query, scoped to ONE organization.
 *
 * `organizationId` is part of the provider key, so switching organizations
 * mounts a brand-new QueryClient and the previous tenant's cached rows are
 * discarded outright rather than being invalidated and possibly re-read
 * (spec §100). In a multi-tenant accounting product, showing the previous
 * organization's figures for even one frame is a data leak, not a glitch.
 *
 * Query keys additionally carry the organization id (see useOrgQuery) as a
 * second line of defence in case a client is ever reused.
 */

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Financial data goes stale the moment someone posts a document.
        // Short and explicit beats a long default that shows an old balance.
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          // Never retry what will fail identically: auth, permission,
          // validation and not-found are all deterministic.
          if (error instanceof ApiError) {
            if (error.status === 429) return failureCount < 2;
            if (error.status < 500) return false;
          }
          return failureCount < 2;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      },
      mutations: {
        // A mutation is never retried automatically. Replaying a POST that
        // may already have posted a journal is exactly what the idempotency
        // key exists to make safe, and retrying without one is unsafe.
        retry: false,
      },
    },
  });
}

/**
 * MUST be rendered with `key={organizationId}` by the app shell. The key is
 * what discards the previous tenant's cache: React unmounts this subtree and
 * the QueryClient it held goes with it, so there is no window in which a
 * stale organization's rows could paint. Swapping the client via state
 * instead would keep the old one alive for a render.
 */
export function QueryProvider({ children }: { children: React.ReactNode }) {
  // Lazy initializer: one client per mount, created on the client only.
  const [client] = React.useState(createQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
