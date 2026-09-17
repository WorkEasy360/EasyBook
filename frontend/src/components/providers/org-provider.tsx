"use client";

import * as React from "react";
import type { Role } from "@/lib/authz/permissions";
import { roleHasAll, roleHasAny, roleHasPermission, type Permission } from "@/lib/authz/permissions";

/**
 * Active-organization context for Client Components.
 *
 * Carries SETTINGS and IDENTITY only — never financial data. The organization
 * id is here because every query key must include it (tenant-safe caching,
 * spec §100), and currency/timezone because almost every cell needs them.
 *
 * Seeded once by the app shell from the server session; nothing re-fetches it.
 */

export interface OrgContextValue {
  organizationId: string;
  organizationName: string;
  role: Role | null;
  /** ISO 4217, from Organization.default_currency. */
  currency: string;
  /** IANA zone, from Organization.timezone. */
  timeZone: string;
  locale: string;
  /** 1–12. Drives the default report period. */
  fiscalYearStartMonth: number;
}

const OrgContext = React.createContext<OrgContextValue | null>(null);

export function OrgProvider({
  value,
  children,
}: {
  value: OrgContextValue;
  children: React.ReactNode;
}) {
  // Memoised on the fields, not the object identity: the shell rebuilds this
  // literal on every render, and a new context value would re-render every
  // consumer for nothing.
  const { organizationId, organizationName, role, currency, timeZone, locale, fiscalYearStartMonth } = value;
  const stable = React.useMemo<OrgContextValue>(
    () => ({ organizationId, organizationName, role, currency, timeZone, locale, fiscalYearStartMonth }),
    [organizationId, organizationName, role, currency, timeZone, locale, fiscalYearStartMonth],
  );
  return <OrgContext.Provider value={stable}>{children}</OrgContext.Provider>;
}

export function useOrg(): OrgContextValue {
  const context = React.useContext(OrgContext);
  if (!context) {
    throw new Error("useOrg must be used inside the authenticated app shell.");
  }
  return context;
}

/**
 * Permission check for the current role.
 *
 * A UI hint (spec §62): it decides what to hide or disable so the interface
 * does not offer an action that will 403. Django remains the authority, and a
 * 403 arriving anyway is handled as an ordinary outcome.
 */
export function useCan(): {
  can: (permission: Permission) => boolean;
  canAny: (permissions: readonly Permission[]) => boolean;
  canAll: (permissions: readonly Permission[]) => boolean;
  role: Role | null;
} {
  const { role } = useOrg();
  return React.useMemo(
    () => ({
      can: (permission: Permission) => roleHasPermission(role, permission),
      canAny: (permissions: readonly Permission[]) => roleHasAny(role, permissions),
      canAll: (permissions: readonly Permission[]) => roleHasAll(role, permissions),
      role,
    }),
    [role],
  );
}

/** Formatting settings, derived from the same context. */
export interface FormatSettings {
  currency: string;
  timeZone: string;
  locale: string;
}

/**
 * Falls back to INR/Asia/Kolkata rather than throwing, so a display component
 * used outside the shell (a test, a print view) still renders sensibly.
 */
export function useFormatSettings(): FormatSettings {
  const context = React.useContext(OrgContext);
  return React.useMemo(
    () => ({
      currency: context?.currency ?? "INR",
      timeZone: context?.timeZone ?? "Asia/Kolkata",
      locale: context?.locale ?? "en-IN",
    }),
    [context?.currency, context?.timeZone, context?.locale],
  );
}
