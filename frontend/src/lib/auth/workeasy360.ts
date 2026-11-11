import "server-only";

import { callUpstream, decodeJson } from "@/lib/api/upstream";
import { sameSitePath } from "@/lib/redirect";

/**
 * Workeasy360 sign-in, the BFF half (backend/sso/ is the other).
 *
 * EasyBook is a Workeasy360 product: people sign in with their Workeasy360
 * account. Django does the protocol work (it holds the client key and verifies
 * the ID token); these helpers only carry the browser there and back.
 */

/**
 * What the sign-in must remember while the browser is at Workeasy360: the
 * Django-signed flow blob (state, nonce, PKCE verifier) and where to land,
 * kept in an httpOnly cookie for SSO_FLOW_MAX_AGE_SECONDS. Its name is
 * `cookieName("ssoFlow")` - `__Host-eb_sso` whenever cookies are Secure - and
 * that is what makes it trustworthy: a cookie a sibling subdomain plants under
 * the plain name is never read, so nobody can hand a victim the attacker's own
 * flow and sign them into the attacker's books (src/lib/auth/cookie-names.ts).
 * A `__Host-` cookie must have Path=/, so it is no longer scoped to the
 * callback path; it is single-use, signed, and gone after ten minutes.
 */
/** Mirrors sso.flow.FLOW_MAX_AGE_SECONDS in the backend. */
export const SSO_FLOW_MAX_AGE_SECONDS = 600;

export interface SignInMethods {
  password: boolean;
  workeasy360: boolean;
  /** "Sign out of EasyBook and Workeasy360" is offered (RP-initiated logout is configured). */
  workeasy360SignOut: boolean;
}

/**
 * Asks Django which sign-in methods are on, or null when it cannot be asked
 * (unreachable, timed out, not a 200). Callers choose what to assume.
 */
export async function fetchSignInMethods(
  options: { revalidateSeconds?: number; timeoutMs?: number } = {},
): Promise<SignInMethods | null> {
  try {
    const response = await callUpstream({
      path: "/auth/methods/",
      ...(options.revalidateSeconds ? { next: { revalidate: options.revalidateSeconds } } : { cache: "no-store" as const }),
      timeoutMs: options.timeoutMs ?? 5_000,
    });
    const body = decodeJson(response) as { password?: unknown; workeasy360?: unknown; workeasy360_sign_out?: unknown } | null;
    if (response.status === 200 && body) {
      return {
        password: body.password === true,
        workeasy360: body.workeasy360 === true,
        workeasy360SignOut: body.workeasy360_sign_out === true,
      };
    }
  } catch {
    // fall through
  }
  return null;
}

/**
 * Asks Django which sign-in methods to offer. If it cannot be reached, the
 * page falls back to showing both: the buttons still go through Django, which
 * refuses whatever is switched off, so guessing wrong costs a message, never a
 * bypass.
 */
export async function readSignInMethods(options: { revalidateSeconds?: number } = {}): Promise<SignInMethods> {
  // Never offer signing out of Workeasy360 on a guess: it is a navigation that
  // would land on an error if it is not configured.
  return (await fetchSignInMethods(options)) ?? { password: true, workeasy360: true, workeasy360SignOut: false };
}

export { WORKEASY360_SIGN_OUT_PATH } from "./workeasy360-paths";

/**
 * Only same-site absolute PATHS are honoured: "//evil.com", "/\evil.com",
 * "https://evil.com" and "/<tab>/evil.com" (which a browser reads as
 * "//evil.com") are all refused - open redirect via `next`. The one check every
 * redirect target goes through: src/lib/redirect.ts.
 */
export function safePath(target: string | null | undefined): string | null {
  return sameSitePath(target);
}

/** The Workeasy360 launcher's ?organization=, forwarded only when it is a UUID. */
export function organizationParam(value: string | null | undefined): string {
  return value && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ? value : "";
}

/** Messages for /login?sso_error=<code>. Unknown codes get the generic one. */
const SSO_ERROR_MESSAGES: Record<string, string> = {
  unavailable: "Workeasy360 sign-in is not available right now. Please try again in a moment.",
  cancelled: "Sign-in with Workeasy360 was cancelled.",
  expired: "That sign-in took too long or was opened in another browser. Please try again.",
  not_entitled: "Your organization does not have EasyBook yet. Start a free trial or buy it in Workeasy360.",
  no_seat: "You do not have an EasyBook seat. Ask your Workeasy360 administrator to assign you one.",
  workeasy360_access: "Workeasy360 did not allow this sign-in for your organization. Ask your Workeasy360 administrator.",
  email_unverified:
    "Verify your email address in Workeasy360 first. An EasyBook account already uses it, and it can only be linked once the address is verified.",
  account_conflict: "This EasyBook account is already linked to a different Workeasy360 account. Contact support.",
  membership_inactive: "Your access to this organization in EasyBook was removed. Ask an owner or admin to restore it.",
  account_disabled: "This EasyBook account is disabled. Contact support.",
};

export function ssoErrorMessage(code: string | undefined): string | null {
  if (!code) return null;
  return SSO_ERROR_MESSAGES[code] ?? "Sign-in with Workeasy360 could not be completed. Please try again.";
}

/**
 * An `error=` Workeasy360 sent the browser back with
 * (Workeasy360 platform/ecosystem/protocol.py), mapped onto the page's
 * vocabulary. `membership_inactive` here is the Workeasy360 membership, not
 * EasyBook's, so it is not confused with the backend code of the same name.
 */
export function providerErrorCode(error: string | null | undefined): string {
  switch (error) {
    case "access_denied":
      return "cancelled";
    case "product_not_entitled":
      return "not_entitled";
    case "product_no_seat":
      return "no_seat";
    case "membership_inactive":
    case "organization_inactive":
    case "access_policy_denied":
    case "integration_suspended":
      return "workeasy360_access";
    case "server_error":
    case "temporarily_unavailable":
    // Workeasy360 says EasyBook is not registered to use its sign-in.
    case "unauthorized_client":
      return "unavailable";
    // Only answered to prompt=none, which EasyBook never sends: start again.
    case "interaction_required":
    case "login_required":
      return "expired";
    default:
      return "failed";
  }
}

/** A code from Django's callback endpoint (backend/sso/views.py). */
export function backendErrorCode(code: string | null | undefined): string {
  switch (code) {
    case "flow_expired":
    case "state_mismatch":
      return "expired";
    case "workeasy360_unavailable":
      return "unavailable";
    case "email_unverified":
    case "account_conflict":
    case "membership_inactive":
    case "account_disabled":
      return code;
    default:
      return "failed";
  }
}
