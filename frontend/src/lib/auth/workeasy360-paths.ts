/**
 * Client-safe Workeasy360 paths (src/lib/auth/workeasy360.ts is server-only).
 *
 * "Sign out of EasyBook and Workeasy360": after EasyBook's own sign-out, this
 * BFF route sends the browser on to Workeasy360, which asks before signing
 * anyone out and then returns here.
 */
export const WORKEASY360_SIGN_OUT_PATH = "/api/auth/workeasy360/logout";

/**
 * Where a page goes when Workeasy360 says the organization's access to
 * EasyBook has ended (backend/sso/entitlement.py): one explanation instead of
 * every page failing on its own.
 */
export const ACCESS_ENDED_PATH = "/access-ended";

/**
 * Why a person (not the organization) lost access, as Workeasy360 answers a per-person check
 * (backend/sso/entitlement.py): their account or membership was disabled or removed, or their seat was
 * unassigned. The only reasons the access-ended page words differently.
 */
export const PERSON_REASONS = ["membership_inactive", "no_seat"] as const;
export type PersonReason = (typeof PERSON_REASONS)[number];

export function personReasonOf(value: unknown): PersonReason | null {
  return PERSON_REASONS.find((reason) => reason === value) ?? null;
}

/**
 * The same page for the refusals: the organization's access has ended, a person's has (`reason`), or
 * Workeasy360 refused EasyBook's own check so it cannot be confirmed (`workeasy360_access_unverified`) -
 * which must not be called "ended".
 */
export type AccessEndedPath =
  | "/access-ended"
  | "/access-ended?reason=unverified"
  | "/access-ended?reason=sign_in_required"
  | "/access-ended?reason=membership_inactive"
  | "/access-ended?reason=no_seat";

export function accessEndedPath(code: unknown, reason?: unknown): AccessEndedPath {
  if (code === "workeasy360_access_unverified") return "/access-ended?reason=unverified";
  // A session opened before Workeasy360 became the only way in: it has no Workeasy360 account to check.
  if (code === "workeasy360_sign_in_required") return "/access-ended?reason=sign_in_required";
  const person = personReasonOf(reason);
  if (person === "membership_inactive") return "/access-ended?reason=membership_inactive";
  if (person === "no_seat") return "/access-ended?reason=no_seat";
  return ACCESS_ENDED_PATH;
}
