"""The sign-in refusals EasyBook's views answer the BFF with.

The connector refuses a sign-in with ``workeasy360_sso.errors.SignInRefused`` (its own code, plus the stage,
the provider's OAuth ``error`` and the token endpoint's status). EasyBook's BFF already speaks a vocabulary
of its own (frontend/src/lib/auth/workeasy360.ts ``backendErrorCode``), so every refusal is mapped back onto
the codes this module has always returned. ``detail`` goes to the log only.
"""

from workeasy360_sso.errors import (
    STAGE_BINDING,
    STAGE_CONFIGURATION,
    STAGE_FLOW,
    STAGE_ID_TOKEN,
    STAGE_START,
    STAGE_TOKEN,
    SignInRefused,
)


class SSOError(Exception):
    """A sign-in that must not complete. ``code`` is safe to show a user's
    browser (a fixed vocabulary); ``detail`` is for logs only."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


#: Codes that mean "Workeasy360 could not be reached or answered oddly, or this side is misconfigured"
#: (retry later: 503 ``workeasy360_unavailable``) rather than "this sign-in is refused".
UNAVAILABLE = frozenset({"provider_unavailable", "provider_misconfigured"})

#: EasyBook's own refusals (sso/provisioning.py) and the connector's codes EasyBook has always used under the
#: same name: passed through unchanged.
_PASSED_THROUGH = frozenset(
    {
        "email_required",
        "email_unverified",
        "account_conflict",
        "account_disabled",
        "organization_inactive",
        "membership_inactive",
        "provider_misconfigured",
        "wrong_product",
        "wrong_organization",
    }
)


def from_refusal(refusal: SignInRefused) -> SSOError:
    """The connector's refusal in EasyBook's vocabulary."""
    detail = f"{refusal.stage or '-'}: {refusal.reason}"
    if refusal.provider_error:
        detail = f"{detail} (provider error {refusal.provider_error})"
    if refusal.code in ("unavailable", "rate_limited") or refusal.stage in (STAGE_START, STAGE_CONFIGURATION):
        # Not configured, discovery or the key set unreadable, the token endpoint unreachable, a 5xx or a 429.
        return SSOError("provider_unavailable", detail)
    if refusal.stage == STAGE_FLOW:
        # No flow for this state in this browser: another state than the one this flow was started with, or a
        # flow already spent (a replayed callback). The code is checked before anything else.
        return SSOError("state_mismatch" if refusal.reason == "no_pending_flow" else "invalid_request", detail)
    if refusal.stage == STAGE_TOKEN:
        if refusal.provider_error == "temporarily_unavailable":
            return SSOError("provider_unavailable", detail)
        if refusal.reason == "token_response_incomplete":
            return SSOError("invalid_token", detail)  # a 200 with no ID token
        # invalid_grant is routine (an expired code, a membership changed since the redirect): the user simply
        # starts again.
        return SSOError("sign_in_refused", detail)
    if refusal.stage == STAGE_ID_TOKEN:
        return SSOError("invalid_token", detail)
    if refusal.stage == STAGE_BINDING:
        if refusal.code == "session_ended":
            # The Workeasy360 sign-in this one came from was signed out (back-channel logout) while it was in
            # flight: start again.
            return SSOError("flow_expired", detail)
        if refusal.reason == "no_organization_claim":
            return SSOError("invalid_token", detail)
    if refusal.code in _PASSED_THROUGH:
        return SSOError(refusal.code, detail)
    return SSOError("sign_in_refused", detail)
