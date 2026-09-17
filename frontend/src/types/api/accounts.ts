import type { DateTimeString, UUID } from "@/lib/api/types";
import type { Role } from "@/lib/authz/permissions";

/**
 * Accounts API contract.
 *
 * VERIFIED against backend/accounts/serializers.py + views.py (serializer
 * dump) and live responses from the dev tenant on 2026-09-17:
 * GET auth/me/, GET organizations/ (a BARE array, not paginated),
 * GET organizations/members/ (a bare array of ACTIVE memberships only).
 *
 * There is no endpoint to update an organization, invite a member or change a
 * role, and no currency list endpoint — see the settings pages for the
 * blockers this leaves.
 */

/** backend/accounts/serializers.py :: UserSerializer (GET auth/me/). All read-only. */
export interface User {
  id: UUID;
  email: string;
  first_name: string;
  last_name: string;
}

/** backend/accounts/serializers.py :: OrganizationSerializer */
export interface Organization {
  id: UUID;
  name: string;
  legal_name: string;
  /** Currency primary key — an ISO 4217 code such as "INR". */
  default_currency: string;
  /** IANA zone, e.g. "Asia/Kolkata". Drives all instant rendering. */
  timezone: string;
  /** 1–12; the backend's default is 4 (April). */
  fiscal_year_start_month: number;
  /**
   * A blank CharField: "" when unset (verified live), never null. Typed
   * `string | null` so existing callers that guard for null still compile.
   */
  gstin: string | null;
  is_active: boolean;
  /** The requesting user's role in THIS organization. Null if unresolvable. */
  role: Role | null;
  created_at: DateTimeString;
}

/**
 * POST organizations/ — backend/accounts/serializers.py :: OrganizationCreateSerializer.
 *
 * Only `name` and `default_currency` are required. `default_currency` must be
 * an existing Currency code (seeded: INR, USD, EUR, GBP, AED, SGD). The
 * serializer does not validate `timezone` or the month range, so the form
 * must. The creator becomes the organization's owner. No fiscal year is
 * created.
 */
export interface OrganizationCreateInput {
  name: string;
  default_currency: string;
  legal_name?: string;
  timezone?: string;
  fiscal_year_start_month?: number;
  gstin?: string;
}

/** backend/accounts/serializers.py :: MembershipSerializer */
export interface Membership {
  id: UUID;
  user: User;
  role: Role;
  is_active: boolean;
  created_at: DateTimeString;
}

/** POST /auth/login/ — SimpleJWT TokenObtainPairView. */
export interface LoginResponse {
  access: string;
  refresh: string;
}

export function displayName(user: User): string {
  const full = `${user.first_name} ${user.last_name}`.trim();
  return full.length > 0 ? full : user.email;
}

export function initialsOf(user: User): string {
  const first = user.first_name.trim()[0];
  const last = user.last_name.trim()[0];
  if (first && last) return `${first}${last}`.toUpperCase();
  if (first) return first.toUpperCase();
  return user.email.slice(0, 2).toUpperCase();
}
