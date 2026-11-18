"""Housekeeping of the WorkEasy360 sign-in connector's tables (class C: no organization, no customer effect).

What ``manage.py w360sso_cleanup`` does, with one difference that matters here. The connector's command also
deletes open session records whose Django session no longer exists - but no EasyBook session is a Django
session (sso/flow.py), so it would delete every record, and with it what back-channel logout ends and what
sso/sessions.py checks each token against. EasyBook's sessions are refresh-token families instead, so an
open record is dropped only once its person holds no live refresh token at all: none of their sessions,
that one included, can be refreshed again.

Ended records are kept longer than a refresh token lives, so a token of an ended session is refused for as
long as it could otherwise be used (sso/sessions.py).
"""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from workeasy360_sso.flow import purge_expired
from workeasy360_sso.models import EndedProviderSession, ProductSession, ReceivedLogoutToken

#: Ended session records are kept at least this long, and always longer than a refresh token lives.
KEEP_ENDED_SESSIONS = timedelta(days=30)
#: A logout token lives two minutes, and an ended Workeasy360 sign-in only matters while a sign-in begun
#: before it could still complete: a day of both is ample.
KEEP_LOGOUT_RECORDS = timedelta(days=1)
#: An open record younger than this is never dropped, whatever the tokens say (its sign-in may still be
#: committing).
MIN_OPEN_SESSION_AGE = timedelta(days=1)


def keep_ended_sessions() -> timedelta:
    return max(KEEP_ENDED_SESSIONS, settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"] + timedelta(days=1))


def purge(*, now=None) -> dict:
    now = now or timezone.now()
    flows = purge_expired()
    jtis, _ = ReceivedLogoutToken.objects.filter(received_at__lt=now - KEEP_LOGOUT_RECORDS).delete()
    sids, _ = EndedProviderSession.objects.filter(ended_at__lt=now - KEEP_LOGOUT_RECORDS).delete()
    ended, _ = ProductSession.objects.filter(ended_at__lt=now - keep_ended_sessions()).delete()
    holders = OutstandingToken.objects.filter(
        user__isnull=False, expires_at__gt=now, blacklistedtoken__isnull=True
    ).values("user_id")
    lapsed, _ = (
        ProductSession.objects.filter(ended_at__isnull=True, created_at__lt=now - MIN_OPEN_SESSION_AGE)
        .exclude(identity__user_id__in=holders)
        .delete()
    )
    return {"flows": flows, "logout_jtis": jtis, "ended_sids": sids, "ended_sessions": ended, "lapsed_sessions": lapsed}


@shared_task
def purge_workeasy360_records_task():
    return purge()
