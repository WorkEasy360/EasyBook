"""Is this organization still entitled to EasyBook? Asked of Workeasy360 on a cadence, never assumed.

A Workeasy360 sign-in proves the organization held EasyBook at that moment; EasyBook's own session then
lives on - its refresh token rotates, so an active user is never signed out. The Workeasy360 integration
contract closes that gap (product integration guide section 6, entitlement API): re-check on the
advertised cadence and stop serving an organization whose access has ended - a trial that ran out, a
lapsed or cancelled subscription - within that window.

The question, its cache and its rules are the workeasy360-sso connector's (``workeasy360_sso.entitlements``);
this module decides *what* EasyBook asks and what it does with the answer:

- One answer per question, shared by every user and process through the cache, for the provider's
  ``revalidate_after`` (900 s at most, 30 s at least). A denial is asked again sooner
  (``DENIAL_RECHECK_SECONDS``), so a renewal takes effect quickly.
- A signed-in request whose user has an ``ExternalIdentity`` for the configured issuer asks per person:
  (Workeasy360 organization, the ``sub`` the person signed in with). Workeasy360 then also denies with
  ``membership_inactive`` (account disabled or locked, membership suspended or removed) or ``no_seat``
  (a per-seat product whose seat was unassigned). The cache key, the local copy, the single flight, the
  back-off and the denial re-check are all per that question. Without an identity the question is the
  organization's alone. Background work never has a person: it asks per organization, and trusts a denial
  for ``BACKGROUND_DENIAL_SECONDS`` (``background_gate``).
- An outage (5xx, 429, timeout, network, an unreadable key, anything that is not an answer) is never a
  denial. The last known answer stands; with none, access continues - the provider's own rule, so an outage
  cannot lock out paying customers. After a failure the next attempt, from any process, waits
  ``FAILURE_BACKOFF_SECONDS``.
- A refusal is not an outage: when Workeasy360 answers but refuses EasyBook's own client (400/401/403 -
  a revoked or wrong credential, a suspended integration), access is refused too
  (``workeasy360_access_unverified``). A refusal is about the *client*, so it closes the whole client for
  ``CLIENT_REFUSED_SECONDS``: every question - a person's, an organization's, background work's - fails
  closed without being asked; then one is asked again. A 429 within ``RATE_LIMIT_AFTER_REFUSAL_SECONDS`` of
  the last refusal is Workeasy360's failed-client-authentication limiter, so it is a refusal too.
- With ``WORKEASY360_SSO_ONLY`` on, a user with no ``ExternalIdentity`` (a session opened before SSO-only was
  switched on) is refused (``workeasy360_sign_in_required``) in an organization linked to Workeasy360: there
  is no subject to check per person, so the session could otherwise outlive the person's removal in
  Workeasy360 for as long as its refresh token keeps rotating. They sign in with Workeasy360, which links the
  account by verified e-mail. With SSO-only off (development) the organization is asked, as before.
- A known denial never lapses into access: it is kept without expiry, and every process also keeps its
  own copy of the last answers, so a cache outage or eviction cannot hand access back. A first check is
  never waved through because another request happens to be asking.
- The shared cache is an optimisation, never a dependency: every cache operation fails open (the
  connector's, and the subject lookup's here), so a Redis outage costs extra provider questions and a
  subject query per request, never a 500.
- Only organizations linked to Workeasy360 are checked: an EasyBook-only organization has no
  Workeasy360 entitlement to lose.
- Reads need ``allow``; writes also need ``write_allowed`` - a suspended subscription is read-only, so the
  customer can still see and export their books. A view that only reads but answers POST (a document
  download, a question to Ask Books) says so with ``entitlement_read = True``.

Enforced for every organization-scoped request through ``core.access`` (registered in
``SsoConfig.ready``); sign-in, ``auth/me/`` and the organization list stay reachable so the person can
see why and sign out.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.dispatch import receiver
from rest_framework.permissions import SAFE_METHODS
from workeasy360_sso import entitlements as connector
from workeasy360_sso.conf import ConfigurationError
from workeasy360_sso.signals import entitlement_checked

from core.exceptions import ApplicationError
from sso.config import connector as connector_config
from sso.config import is_configured
from sso.models import ExternalIdentity

logger = logging.getLogger(__name__)

#: Used when the provider names no cadence; also the longest an answer is trusted.
DEFAULT_REVALIDATE_SECONDS = connector.MAX_ALLOW_TTL
MIN_REVALIDATE_SECONDS = connector.MIN_ALLOW_TTL
#: A denial is re-asked this soon for a waiting person, so an owner who renews is not kept out for the window.
DENIAL_RECHECK_SECONDS = connector.PERSON_DENY_RECHECK
#: Background work trusts a denial this long: an expired organization's jobs must not cost a question a
#: minute each (the window an allow is trusted for).
BACKGROUND_DENIAL_SECONDS = connector.BACKGROUND_DENY_RECHECK
#: How long a refusal of EasyBook's own client closes every question (and is re-asked after, whoever asks).
CLIENT_REFUSED_SECONDS = connector.CLIENT_REFUSAL_HOLD
#: A 429 this soon after the last refusal is the failed-client-authentication limiter, so it is a refusal.
RATE_LIMIT_AFTER_REFUSAL_SECONDS = connector.REFUSAL_429_WINDOW
#: After a failed check, how long before the provider is asked again (the last answer stands meanwhile).
FAILURE_BACKOFF_SECONDS = connector.OUTAGE_BACKOFF
#: How long the (user -> Workeasy360 subject) lookup is kept, so a request costs no extra query. Only a
#: found subject is kept: caching "no identity" could outlive the sign-in that creates one.
SUBJECT_CACHE_SECONDS = 300

ACCESS_ENDED = connector.ACCESS_ENDED
ACCESS_UNVERIFIED = connector.ACCESS_UNVERIFIED
SIGN_IN_REQUIRED = "workeasy360_sign_in_required"
READ_ONLY = connector.READ_ONLY
#: The reason recorded when Workeasy360 refused EasyBook's own client.
REFUSED = "refused"
#: The reason of an ``Access`` for a user who must sign in with Workeasy360 first (SSO-only, no identity).
SIGN_IN_REQUIRED_REASON = "sign_in_required"
#: Reasons Workeasy360 gives only for a per-person question (design section 4).
PERSON_REASONS = ("membership_inactive", "no_seat")


@dataclass(frozen=True)
class Access:
    allowed: bool
    write_allowed: bool
    #: The provider's reason (``entitled``, ``expired``, ``not_entitled``, ``suspended``...), ``refused``
    #: when Workeasy360 refused EasyBook's client, or ``unchecked`` when no answer was ever available.
    reason: str


UNCHECKED = Access(allowed=True, write_allowed=True, reason="unchecked")


_cache_warned_at = 0.0
CACHE_WARNING_INTERVAL_SECONDS = 60


def _cache_failed(operation: str, exc: Exception) -> None:
    """A shared-cache error is never the caller's problem: note it (at most once a minute) and carry on."""
    global _cache_warned_at
    now = time.time()
    if now - _cache_warned_at >= CACHE_WARNING_INTERVAL_SECONDS:
        _cache_warned_at = now
        logger.warning("sso.entitlement_cache_unavailable", extra={"operation": operation, "error": type(exc).__name__})


def _subject_key(user_id) -> str:
    return f"workeasy360:subject:{user_id}"


def subject_for(user) -> str:
    """The ``sub`` ``user`` signed in with at the configured issuer, or "" when they have no Workeasy360
    identity (then the question is the organization's alone). A found subject is cached, so a request costs
    no query; "no identity" is not (it is rare, and a cached "" could hide an identity created a moment
    later). ``forget_subject`` is still called, after commit, when an identity is created."""
    key = _subject_key(user.pk)
    try:
        cached = cache.get(key)
    except Exception as exc:  # any cache-backend failure is a miss
        _cache_failed("get", exc)
        cached = None
    if cached:
        return cached
    subject = (
        ExternalIdentity.objects.filter(user_id=user.pk, issuer=settings.WORKEASY360_ISSUER)
        .values_list("subject", flat=True)
        .first()
        or ""
    )
    if subject:
        try:
            cache.set(key, subject, SUBJECT_CACHE_SECONDS)
        except Exception as exc:
            _cache_failed("set", exc)
    return subject


def forget_subject(user_id) -> None:
    try:
        cache.delete(_subject_key(user_id))
    except Exception as exc:
        _cache_failed("delete", exc)


def _access(decision: connector.AccessDecision) -> Access:
    if decision.provisional:
        return UNCHECKED
    if decision.code == connector.ACCESS_UNVERIFIED:
        return Access(allowed=False, write_allowed=False, reason=REFUSED)
    return Access(
        allowed=decision.allowed, write_allowed=decision.allowed and decision.write_allowed, reason=decision.reason
    )


def _check(organization, workeasy360_id, subject: str, *, background: bool) -> Access:
    """The cached-or-asked answer for one question. ``background``: a denial is trusted for
    ``BACKGROUND_DENIAL_SECONDS``, not ``DENIAL_RECHECK_SECONDS`` (nobody is waiting on a renewal)."""
    try:
        config = connector_config()
    except ConfigurationError as exc:
        # Configuration that cannot even be loaded (an unreadable key file): nothing can be asked, which is
        # an outage like any other - never a denial, never a 500.
        logger.warning(
            "sso.entitlement_unavailable",
            extra={"code": "provider_misconfigured", "detail": str(exc), "organization_id": str(organization.pk)},
        )
        return UNCHECKED
    decision = connector.check(
        config,
        organization_id=uuid.UUID(str(workeasy360_id)),
        subject=subject or None,
        background=background,
        context={"organization_id": str(organization.pk)},
    )
    return _access(decision)


def access_for(organization, user=None) -> Access | None:
    """Workeasy360's current answer for ``organization`` - and for ``user`` when they signed in with a
    Workeasy360 account - or None when it is not linked to Workeasy360 (or Workeasy360 sign-in is not
    configured here): there is nothing to check."""
    workeasy360_id = getattr(organization, "workeasy360_organization_id", None)
    if not workeasy360_id or not is_configured():
        return None
    person = user is not None and getattr(user, "is_authenticated", False)
    subject = subject_for(user) if person else ""
    if person and not subject and settings.WORKEASY360_SSO_ONLY:
        return Access(allowed=False, write_allowed=False, reason=SIGN_IN_REQUIRED_REASON)
    return _check(organization, workeasy360_id, subject, background=False)


def stored_answer(workeasy360_id, subject: str = "") -> connector.Answer | None:
    """The last answer known for this organization (and person), however old; for tests and support."""
    return connector.last_answer(
        connector_config(), organization_id=uuid.UUID(str(workeasy360_id)), subject=subject or None
    )


@receiver(entitlement_checked, dispatch_uid="sso.entitlement.log_question")
def _log_question(
    sender: Any,
    *,
    organization_id: uuid.UUID,
    subject: str,
    outcome: str,
    answer: connector.Answer | None,
    previous: connector.Answer | None,
    detail: str,
    rate_limited: bool,
    context: dict[str, Any] | None,
    **kwargs: Any,
) -> None:
    """EasyBook's log lines for every question actually put to Workeasy360 (never for a cached answer)."""
    easybook_organization = (context or {}).get("organization_id") or str(organization_id)
    if outcome == "refused":
        # Workeasy360 answered and refused EasyBook's own client (or is rate-limiting a client that was just
        # refused): not an outage. The whole client is closed, fail closed, loudly.
        logger.error(
            "sso.entitlement_refused",
            extra={"detail": detail, "organization_id": easybook_organization, "rate_limited": rate_limited},
        )
    elif outcome == "outage":
        # An outage: never a denial. Logged loudly enough to be noticed - while it lasts, access changes made
        # in Workeasy360 are not reaching EasyBook.
        logger.warning(
            "sso.entitlement_unavailable",
            extra={
                "code": "provider_unavailable",
                "detail": detail,
                "organization_id": easybook_organization,
                "last_known": previous.decision if previous else None,
            },
        )
    elif answer is not None and (
        previous is None or previous.decision != answer.decision or previous.reason != answer.reason
    ):
        logger.info(
            "sso.entitlement_changed",
            extra={
                "organization_id": easybook_organization,
                "decision": answer.decision,
                "reason": answer.reason,
                "per_person": bool(subject),
            },
        )


def background_gate(organization, task: str) -> bool:
    """May ``task`` produce a customer-visible effect for ``organization``? Background work (design
    section 5, class A) needs the organization's own ``allow`` and ``write_allowed``. A denial or a
    refusal skips the work and is logged, never raised: the caller leaves its schedule as it was, so the
    work resumes after a renewal. An outage, or no answer yet, lets it run. An organization not linked to
    Workeasy360 always runs."""
    workeasy360_id = getattr(organization, "workeasy360_organization_id", None)
    if not workeasy360_id or not is_configured():
        return True
    try:
        access = _check(organization, workeasy360_id, "", background=True)
    except Exception as exc:  # a job must never fail because the question could not be asked
        logger.warning(
            "sso.entitlement_unavailable",
            extra={"code": type(exc).__name__, "detail": "", "organization_id": str(organization.pk)},
        )
        return True
    if access.allowed and access.write_allowed:
        return True
    logger.warning(
        "workeasy360.background_skipped",
        extra={
            "organization_id": str(organization.pk),
            "task": task,
            "reason": access.reason if not access.allowed else "read_only",
        },
    )
    return False


def require(organization, *, write: bool, user=None) -> None:
    """Refuse the request when Workeasy360 says the organization's access to EasyBook has ended (or will
    not confirm it), or that it is read-only and this is a write."""
    access = access_for(organization, user)
    if access is None:
        return
    if not access.allowed and access.reason == SIGN_IN_REQUIRED_REASON:
        raise ApplicationError(
            detail=(
                "EasyBook now signs everyone in through Workeasy360. Sign in with Workeasy360 to continue: this"
                " session cannot be checked against your access there."
            ),
            code=SIGN_IN_REQUIRED,
            status_code=403,
        )
    if not access.allowed and access.reason == REFUSED:
        raise ApplicationError(
            detail=(
                "EasyBook cannot confirm your organization's access with Workeasy360 at the moment. Try again in"
                " a few minutes; if this continues, contact support."
            ),
            code=ACCESS_UNVERIFIED,
            status_code=403,
        )
    if not access.allowed:
        error = ApplicationError(detail=_ended_message(access.reason), code=ACCESS_ENDED, status_code=403)
        if access.reason in PERSON_REASONS:
            error.extra = {"reason": access.reason}  # the frontend explains which (core.exceptions)
        raise error
    if write and not access.write_allowed:
        raise ApplicationError(
            detail=(
                "Your organization's EasyBook subscription is read-only at the moment: you can view and export"
                " your books but not change them. An owner can resolve this in Workeasy360."
            ),
            code=READ_ONLY,
            status_code=403,
        )


def _ended_message(reason: str) -> str:
    if reason == "membership_inactive":
        return (
            "Your access to this organization's EasyBook has been removed in Workeasy360. Ask an owner or"
            " administrator of the organization to restore it."
        )
    if reason == "no_seat":
        return (
            "You no longer have an EasyBook seat in this organization. Ask an owner or administrator to"
            " assign one in Workeasy360."
        )
    return "Your organization's access to EasyBook has ended. An owner can renew it in Workeasy360."


def gate(request, view) -> None:
    """The ``core.access`` gate: every organization-scoped request."""
    write = request.method not in SAFE_METHODS and not getattr(view, "entitlement_read", False)
    require(request.organization, write=write, user=request.user)


def clear_local() -> None:
    """Forget this process's copy of the answers, back-offs and refusal marks (tests; the shared cache is
    cleared separately)."""
    connector.reset_state()
