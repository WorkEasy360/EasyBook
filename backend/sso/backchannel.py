"""Back-channel logout (OpenID Connect Back-Channel Logout 1.0), EasyBook's part.

The connector's receiver (``POST /api/v1/auth/workeasy360/backchannel-logout/``, sso/urls.py) verifies the
logout token (signature from the key set, ``iss``, ``aud``, ``iat``/``exp``, the back-channel event, no
``nonce``, ``typ: logout+jwt``), refuses a replayed ``jti`` and ends the session records its sign-ins opened:

- a token naming ``sid`` (one Workeasy360 sign-in ended: "sign out of this device", an account switch, a
  revoked session): the EasyBook sessions that sign-in opened, nothing else - the same person's other
  devices stay signed in;
- a token naming only ``sub`` ("sign out everywhere", a password change or reset): every session the
  connector recorded for that person.

An ended record ends the EasyBook session at once: every token of it names the record and is refused from
then on (sso/sessions.py). EasyBook's sessions are older than the connector's records, though: one opened
before this release (or by a password) names none. So for "sign out everywhere" EasyBook also revokes every
refresh token the person holds; such a session then ends at its next refresh, within one access-token
lifetime (``JWT_ACCESS_MINUTES``).

This runs inside the connector's transaction: if it fails, nothing is recorded (the ``jti`` included) and
Workeasy360 is answered 503 and delivers the token again.
"""

import logging
from typing import Any

from django.dispatch import receiver
from workeasy360_sso.signals import backchannel_logout

from accounts.models import Organization
from audit.models import AuditLog
from audit.services import record
from sso.models import ExternalIdentity
from sso.provisioning import revoke_refresh_tokens

logger = logging.getLogger(__name__)

METHOD = "workeasy360_backchannel"


@receiver(backchannel_logout, dispatch_uid="sso.backchannel.end_easybook_sessions")
def end_easybook_sessions(
    sender: Any, *, issuer: str, subject: str, sid: str, product_sessions: list, **kwargs: Any
) -> None:
    everywhere = bool(subject) and not sid
    revoked = 0
    if everywhere:
        identity = ExternalIdentity.objects.select_related("user").filter(issuer=issuer, subject=subject).first()
        if identity is not None:
            revoked = revoke_refresh_tokens(identity.user)
    scope = "everywhere" if everywhere else "session"
    for product_session in product_sessions:
        _audit(product_session, scope=scope, refresh_tokens_revoked=revoked)
    logger.info(
        "sso.backchannel_logout",
        extra={"scope": scope, "sessions_ended": len(product_sessions), "refresh_tokens_revoked": revoked},
    )


def _audit(product_session, *, scope: str, refresh_tokens_revoked: int) -> None:
    """One audit row per EasyBook session ended, in the organization it was signed in to."""
    organization_id = (
        Organization.objects.filter(workeasy360_organization_id=product_session.organization_id)
        .values_list("id", flat=True)
        .first()
    )
    if organization_id is None:
        return  # the organization is no longer linked here: there is no tenant to record it in
    user = product_session.identity.user
    record(
        organization_id=organization_id,
        action=AuditLog.Action.DELETE,
        object_type="sign_in",
        object_id=user.id,
        actor=None,
        changes={
            "method": METHOD,
            "scope": scope,
            "workeasy360_subject": product_session.identity.subject,
            "refresh_tokens_revoked": refresh_tokens_revoked,
        },
    )
