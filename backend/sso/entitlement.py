"""Is this organization still entitled to EasyBook? Asked of Workeasy360 on a cadence, never assumed.

A Workeasy360 sign-in proves the organization held EasyBook at that moment; EasyBook's own session then
lives on - its refresh token rotates, so an active user is never signed out. The Workeasy360 integration
contract closes that gap (product integration guide section 6, entitlement API): re-check on the
advertised cadence and stop serving an organization whose access has ended - a trial that ran out, a
lapsed or cancelled subscription - within that window.

- One answer per Workeasy360 organization, shared by every user and process through the cache, for the
  provider's ``revalidate_after`` (900 s). A denial is asked again sooner (``DENIAL_RECHECK_SECONDS``),
  so a renewal takes effect quickly.
- A signed-in request whose user has an ``ExternalIdentity`` for the configured issuer asks per person:
  (Workeasy360 organization, the ``sub`` the person signed in with). Workeasy360 then also denies with
  ``membership_inactive`` (account disabled or locked, membership suspended or removed) or ``no_seat``
  (a per-seat product whose seat was unassigned). The cache key, the local copy, the single flight, the
  back-off and the denial re-check are all per that key. Without an identity the question is the
  organization's alone. Background work never has a person: it asks per organization, and trusts a denial
  for ``BACKGROUND_DENIAL_SECONDS`` (``background_gate``).
- An outage (5xx, 429, timeout, network, an unreadable key) is never a denial. The last known answer
  stands; with none, access continues - the provider's own rule, so an outage cannot lock out paying
  customers. After a failure the next attempt waits ``FAILURE_BACKOFF_SECONDS``.
- A refusal is not an outage: when Workeasy360 answers but refuses EasyBook's own client (400/401/403 -
  a revoked or wrong credential, a suspended integration), access is refused too
  (``workeasy360_access_unverified``). A refusal is about the *client*, not one organization or person, so
  it closes the whole client for ``DENIAL_RECHECK_SECONDS``: every question - a person's, an
  organization's, background work's - fails closed without being asked; then one is asked again. Workeasy360
  answers 429 once a client has failed to authenticate ~10 times a minute; a 429 that arrives soon after a
  refusal (``RATE_LIMIT_AFTER_REFUSAL_SECONDS``) is that limiter, so it is a refusal too, not an outage -
  otherwise most people would never learn of the revocation. Keeping on serving would let every expired
  organization in for as long as nobody noticed.
- With ``WORKEASY360_SSO_ONLY`` on, a user with no ``ExternalIdentity`` (a session opened before SSO-only was
  switched on) is refused (``workeasy360_sign_in_required``) in an organization linked to Workeasy360: there
  is no subject to check per person, so the session could otherwise outlive the person's removal in
  Workeasy360 for as long as its refresh token keeps rotating. They sign in with Workeasy360, which links the
  account by verified e-mail. With SSO-only off (development) the organization is asked, as before.
- A known denial never lapses into access: it is kept without expiry, and every process also keeps its
  own copy of the last answers, so a cache outage or eviction cannot hand access back. A first check is
  never waved through because another request happens to be asking.
- The shared cache is an optimisation, never a dependency: every cache operation here fails open (a read
  is a miss, a write or delete is a no-op, ``add`` says "unknown"), so a Redis outage costs extra provider
  questions and a subject query per request, never a 500. This module's own per-process copy then stands in.
- Only organizations linked to Workeasy360 are checked: an EasyBook-only organization has no
  Workeasy360 entitlement to lose.
- Reads need ``allow``; writes also need ``write_allowed`` - a suspended subscription is read-only, so the
  customer can still see and export their books. A view that only reads but answers POST (a document
  download, a question to Ask Books) says so with ``entitlement_read = True``.

Enforced for every organization-scoped request through ``core.access`` (registered in
``SsoConfig.ready``); sign-in, ``auth/me/`` and the organization list stay reachable so the person can
see why and sign out.
"""

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.cache import cache
from rest_framework.permissions import SAFE_METHODS

from core.exceptions import ApplicationError
from sso.config import get_client, is_configured
from sso.models import ExternalIdentity
from sso.oidc import SSOError

logger = logging.getLogger(__name__)

#: Used when the provider names no cadence; also the longest an answer is trusted.
DEFAULT_REVALIDATE_SECONDS = 900
MIN_REVALIDATE_SECONDS = 30
#: A denial (or a refusal) is re-asked this soon, so an owner who renews is not kept out for the window.
DENIAL_RECHECK_SECONDS = 60
#: Background work trusts a denial this long: an expired organization's jobs must not cost a question a
#: minute each (the window an allow is trusted for).
BACKGROUND_DENIAL_SECONDS = 900
#: How long the (user -> Workeasy360 subject) lookup is kept, so a request costs no extra query. Only a
#: found subject is kept: caching "no identity" could outlive the sign-in that creates one.
SUBJECT_CACHE_SECONDS = 300
#: After a failed check, how long before the provider is asked again (the last answer stands meanwhile).
FAILURE_BACKOFF_SECONDS = 60
#: One request asks; the others use the last answer until it has.
REFRESH_LOCK_SECONDS = 15
#: How long a last ``allow`` is kept to stand in for the provider during an outage (a denial: forever).
LAST_KNOWN_KEEP_SECONDS = 7 * 24 * 3600
#: Bound on this process's own copy of the last answers.
LOCAL_MAX_ENTRIES = 10_000

ACCESS_ENDED = "workeasy360_access_ended"
ACCESS_UNVERIFIED = "workeasy360_access_unverified"
SIGN_IN_REQUIRED = "workeasy360_sign_in_required"
READ_ONLY = "workeasy360_read_only"
#: The reason recorded when Workeasy360 refused EasyBook's own client.
REFUSED = "refused"
#: The reason of an ``Access`` for a user who must sign in with Workeasy360 first (SSO-only, no identity).
SIGN_IN_REQUIRED_REASON = "sign_in_required"
#: A 429 this soon after a refusal is the failed-client-authentication limiter, so it is a refusal.
RATE_LIMIT_AFTER_REFUSAL_SECONDS = 300
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

# This process's copy of the last answers and of its failure back-offs: the shared cache's stand-in when
# it is down or has evicted an entry.
_local_lock = threading.Lock()
_local: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_local_backoff: dict[str, float] = {}
#: When Workeasy360 last refused this client (0: never) - this process's copy of the shared marker.
_client_refused_at = 0.0
CLIENT_REFUSED_KEY = "workeasy360:entitlement:client_refused"


_cache_warned_at = 0.0
CACHE_WARNING_INTERVAL_SECONDS = 60


def _cache_failed(operation: str, exc: Exception) -> None:
    """A shared-cache error is never the caller's problem: note it (at most once a minute) and carry on."""
    global _cache_warned_at
    now = time.time()
    if now - _cache_warned_at >= CACHE_WARNING_INTERVAL_SECONDS:
        _cache_warned_at = now
        logger.warning("sso.entitlement_cache_unavailable", extra={"operation": operation, "error": type(exc).__name__})


def _cache_get(key: str):
    try:
        return cache.get(key)
    except Exception as exc:  # any cache-backend failure is a miss
        _cache_failed("get", exc)
        return None


def _cache_set(key: str, value, timeout) -> None:
    try:
        cache.set(key, value, timeout)
    except Exception as exc:
        _cache_failed("set", exc)


def _cache_add(key: str, value, timeout):
    """``cache.add``'s answer, or None when the cache cannot say (then the caller asks)."""
    try:
        return cache.add(key, value, timeout)
    except Exception as exc:
        _cache_failed("add", exc)
        return None


def _cache_delete(key: str) -> None:
    try:
        cache.delete(key)
    except Exception as exc:
        _cache_failed("delete", exc)


def _key(workeasy360_organization_id, subject: str | None = None) -> str:
    """The cache key of one question: the organization's, or (organization, person) when a subject is
    given. The subject is opaque and unbounded, so it is hashed into the key."""
    base = f"workeasy360:entitlement:{workeasy360_organization_id}"
    if not subject:
        return base
    return f"{base}:p:{hashlib.sha256(subject.encode()).hexdigest()[:32]}"


def _subject_key(user_id) -> str:
    return f"workeasy360:subject:{user_id}"


def subject_for(user) -> str:
    """The ``sub`` ``user`` signed in with at the configured issuer, or "" when they have no Workeasy360
    identity (then the question is the organization's alone). A found subject is cached, so a request costs
    no query; "no identity" is not (it is rare, and a cached "" could hide an identity created a moment
    later). ``forget_subject`` is still called, after commit, when an identity is created."""
    key = _subject_key(user.pk)
    cached = _cache_get(key)
    if cached:
        return cached
    subject = (
        ExternalIdentity.objects.filter(user_id=user.pk, issuer=settings.WORKEASY360_ISSUER)
        .values_list("subject", flat=True)
        .first()
        or ""
    )
    if subject:
        _cache_set(key, subject, SUBJECT_CACHE_SECONDS)
    return subject


def forget_subject(user_id) -> None:
    _cache_delete(_subject_key(user_id))


def _access(record: dict[str, Any]) -> Access:
    allowed = record.get("decision") == "allow"
    return Access(
        allowed=allowed, write_allowed=allowed and bool(record.get("write_allowed")), reason=str(record.get("reason", ""))
    )


def _fresh_for(answer: dict[str, Any]) -> int:
    if answer.get("decision") != "allow":
        return DENIAL_RECHECK_SECONDS
    try:
        seconds = int(answer.get("revalidate_after", DEFAULT_REVALIDATE_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_REVALIDATE_SECONDS
    return max(MIN_REVALIDATE_SECONDS, min(seconds, DEFAULT_REVALIDATE_SECONDS))


def _last_known(key: str) -> dict[str, Any] | None:
    record = _cache_get(key)
    if record:
        return record
    with _local_lock:
        return _local.get(key)


def _remember(key: str, record: dict[str, Any]) -> None:
    _cache_set(key, record, None if record["decision"] != "allow" else LAST_KNOWN_KEEP_SECONDS)
    with _local_lock:
        _local[key] = record
        _local.move_to_end(key)
        while len(_local) > LOCAL_MAX_ENTRIES:
            _local.popitem(last=False)
        _local_backoff.pop(key, None)


def _backing_off(key: str, now: float) -> bool:
    if _cache_get(f"{key}:backoff"):
        return True
    with _local_lock:
        return _local_backoff.get(key, 0) > now


def _back_off(key: str, now: float) -> None:
    _cache_set(f"{key}:backoff", 1, FAILURE_BACKOFF_SECONDS)
    with _local_lock:
        _local_backoff[key] = now + FAILURE_BACKOFF_SECONDS


def _last_refusal() -> float:
    shared = _cache_get(CLIENT_REFUSED_KEY)
    return max(_client_refused_at, shared if isinstance(shared, (int, float)) else 0.0)


def _client_refused(now: float) -> bool:
    """Is the whole client closed? True for ``DENIAL_RECHECK_SECONDS`` after Workeasy360 refused it."""
    return now - _last_refusal() < DENIAL_RECHECK_SECONDS


def _mark_client_refused(now: float) -> None:
    global _client_refused_at
    _client_refused_at = now
    _cache_set(CLIENT_REFUSED_KEY, now, RATE_LIMIT_AFTER_REFUSAL_SECONDS)


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
    return _ask(organization, str(workeasy360_id), subject, denial_trust=DENIAL_RECHECK_SECONDS)


def _ask(organization, workeasy360_id: str, subject: str, *, denial_trust: int) -> Access:
    key = _key(workeasy360_id, subject)
    known = _last_known(key)
    now = time.time()
    if _client_refused(now):
        # Workeasy360 refused EasyBook's own client a moment ago: nothing can be confirmed, for anyone.
        return Access(allowed=False, write_allowed=False, reason=REFUSED)
    if known and now - known.get("checked_at", 0) < _trusted_for(known, denial_trust):
        return _access(known)
    if _backing_off(key, now):
        # The provider failed a moment ago: the last answer stands, or (none yet) access continues.
        return _access(known) if known else UNCHECKED
    # True: this request asks. False: another one is asking - use the last answer if there is one, and
    # ask too if there is none (a first check is never waved through). None: the cache is down - ask.
    if _cache_add(f"{key}:refreshing", 1, REFRESH_LOCK_SECONDS) is False and known:
        return _access(known)
    try:
        answer = get_client().verify_entitlement(workeasy360_id, subject=subject or None)
    except Exception as exc:  # anything that is not an answer is handled below, never a 500
        limiter = (
            isinstance(exc, SSOError)
            and exc.status == 429
            and now - _last_refusal() < RATE_LIMIT_AFTER_REFUSAL_SECONDS
        )
        if limiter or (isinstance(exc, SSOError) and exc.code == "provider_refused"):
            # Workeasy360 answered and refused EasyBook's own client (or is rate-limiting a client that was
            # just refused): not an outage. Close the whole client, fail closed, loudly.
            logger.error(
                "sso.entitlement_refused",
                extra={"detail": exc.detail if isinstance(exc, SSOError) else "", "organization_id": str(organization.pk)},
            )
            _mark_client_refused(now)
            record = {
                "decision": "deny",
                "reason": REFUSED,
                "write_allowed": False,
                "checked_at": now,
                "fresh_for": DENIAL_RECHECK_SECONDS,
            }
            _remember(key, record)
            return _access(record)
        # An outage: never a denial. Logged loudly enough to be noticed - while it lasts, access changes
        # made in Workeasy360 are not reaching EasyBook. (A non-SSOError - an unreadable key file, a broken
        # HTTP reply - is logged by its type only.)
        logger.warning(
            "sso.entitlement_unavailable",
            extra={
                "code": exc.code if isinstance(exc, SSOError) else type(exc).__name__,
                "detail": exc.detail if isinstance(exc, SSOError) else "",
                "organization_id": str(organization.pk),
                "last_known": known.get("decision") if known else None,
            },
        )
        _back_off(key, now)
        return _access(known) if known else UNCHECKED
    finally:
        _cache_delete(f"{key}:refreshing")
    record = {
        "decision": answer["decision"],
        "reason": str(answer.get("reason", ""))[:64],
        "write_allowed": bool(answer.get("write_allowed")),
        "checked_at": now,
        "fresh_for": _fresh_for(answer),
    }
    _remember(key, record)
    if not known or known.get("decision") != record["decision"] or known.get("reason") != record["reason"]:
        logger.info(
            "sso.entitlement_changed",
            extra={
                "organization_id": str(organization.pk),
                "decision": record["decision"],
                "reason": record["reason"],
                "per_person": bool(subject),
            },
        )
    return _access(record)


def _trusted_for(known: dict[str, Any], denial_trust: int) -> int:
    """How long ``known`` can be used without asking again. A denial's own window is
    ``DENIAL_RECHECK_SECONDS``; background work extends it to ``denial_trust``. A refusal of EasyBook's own
    client is not a denial of the organization (a credential or integration fault someone is fixing): it is
    re-asked every ``DENIAL_RECHECK_SECONDS`` whoever is asking."""
    fresh_for = known.get("fresh_for", 0)
    if known.get("decision") != "allow" and known.get("reason") != REFUSED:
        return max(fresh_for, denial_trust)
    return fresh_for


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
        access = _ask(organization, str(workeasy360_id), "", denial_trust=BACKGROUND_DENIAL_SECONDS)
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
    """Forget this process's copy of the answers (tests; the shared cache is cleared separately)."""
    global _client_refused_at
    with _local_lock:
        _local.clear()
        _local_backoff.clear()
        _client_refused_at = 0.0
