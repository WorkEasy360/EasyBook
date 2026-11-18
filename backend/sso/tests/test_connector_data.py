"""The connector's data in EasyBook: the identity mirror's back-fill (migration ``sso.0002``) and the
housekeeping task (sso/tasks.py)."""

import importlib
import uuid
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken
from workeasy360_sso.models import (
    EndedProviderSession,
    FederatedIdentity,
    PendingSignIn,
    ProductSession,
    ReceivedLogoutToken,
)

from core.tests.factories import make_user
from sso import tasks
from sso.models import ExternalIdentity

ISSUER = "https://accounts.workeasy360.test"
backfill = importlib.import_module("sso.migrations.0002_federated_identity_backfill")


class BackfillTests(TestCase):
    def test_every_identity_is_mirrored_and_nothing_existing_changes(self):
        asha, ravi = make_user(email="asha@example.com"), make_user(email="ravi@example.com")
        linked_at = timezone.now() - timedelta(days=40)
        first = ExternalIdentity.objects.create(user=asha, issuer=ISSUER, subject="sub-asha", last_login_at=linked_at)
        ExternalIdentity.objects.filter(pk=first.pk).update(created_at=linked_at)
        ExternalIdentity.objects.create(user=ravi, issuer=ISSUER, subject="sub-ravi")
        before = list(ExternalIdentity.objects.order_by("subject").values())

        backfill.mirror_identities(apps, None)

        self.assertEqual(list(ExternalIdentity.objects.order_by("subject").values()), before)  # read only
        mirror = FederatedIdentity.objects.get(subject="sub-asha")
        self.assertEqual((mirror.user_id, mirror.issuer, mirror.status), (asha.id, ISSUER, "active"))
        self.assertEqual(mirror.email_at_link, "asha@example.com")
        self.assertEqual(mirror.linked_at, linked_at)
        self.assertEqual(mirror.last_sign_in_at, linked_at)
        self.assertTrue(FederatedIdentity.objects.filter(subject="sub-ravi", user=ravi).exists())

    def test_it_only_adds_and_running_it_again_adds_nothing(self):
        asha, other = make_user(email="asha@example.com"), make_user(email="other@example.com")
        ExternalIdentity.objects.create(user=asha, issuer=ISSUER, subject="sub-asha")
        # A mirror row that already exists - even one that disagrees - is left exactly as it is: the next sign-in
        # corrects it (sso.provisioning.sync_federated_identity), never the migration.
        existing = FederatedIdentity.objects.create(user=other, issuer=ISSUER, subject="sub-asha")
        backfill.mirror_identities(apps, None)
        backfill.mirror_identities(apps, None)
        self.assertEqual(list(FederatedIdentity.objects.values_list("pk", "user_id")), [(existing.pk, other.id)])

    def test_nothing_to_mirror_is_fine(self):
        backfill.mirror_identities(apps, None)
        self.assertFalse(FederatedIdentity.objects.exists())


class PurgeTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = make_user(email="asha@example.com")
        self.identity = FederatedIdentity.objects.create(user=self.user, issuer=ISSUER, subject="sub-asha")

    def session(self, *, age_days, ended_days_ago=None):
        record = ProductSession.objects.create(
            identity=self.identity, session_key=uuid.uuid4().hex, organization_id=uuid.uuid4(),
            created_at=self.now - timedelta(days=age_days),
        )
        if ended_days_ago is not None:
            record.ended_at = self.now - timedelta(days=ended_days_ago)
            record.end_reason = ProductSession.EndReason.BACKCHANNEL
            record.save()
        return record

    def test_spent_flows_and_logout_records_go(self):
        PendingSignIn.objects.create(
            state_hash="a" * 64, binding_hash="b" * 64, nonce="n", code_verifier="v",
            created_at=self.now - timedelta(hours=3), expires_at=self.now - timedelta(hours=2),
        )
        fresh = PendingSignIn.objects.create(
            state_hash="c" * 64, binding_hash="d" * 64, nonce="n", code_verifier="v",
            created_at=self.now, expires_at=self.now + timedelta(minutes=10),
        )
        old_jti = ReceivedLogoutToken.objects.create(jti="old", issuer=ISSUER)
        ReceivedLogoutToken.objects.filter(pk=old_jti.pk).update(received_at=self.now - timedelta(days=2))
        ReceivedLogoutToken.objects.create(jti="new", issuer=ISSUER)
        old_sid = EndedProviderSession.objects.create(issuer=ISSUER, provider_sid="old")
        EndedProviderSession.objects.filter(pk=old_sid.pk).update(ended_at=self.now - timedelta(days=2))
        EndedProviderSession.objects.create(issuer=ISSUER, provider_sid="new")

        result = tasks.purge(now=self.now)

        self.assertEqual((result["flows"], result["logout_jtis"], result["ended_sids"]), (1, 1, 1))
        self.assertEqual(list(PendingSignIn.objects.values_list("pk", flat=True)), [fresh.pk])
        self.assertEqual(list(ReceivedLogoutToken.objects.values_list("jti", flat=True)), ["new"])
        self.assertEqual(list(EndedProviderSession.objects.values_list("provider_sid", flat=True)), ["new"])

    def test_an_ended_session_is_kept_longer_than_a_refresh_token_lives(self):
        refresh_lifetime = settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]
        self.assertGreater(tasks.keep_ended_sessions(), refresh_lifetime)
        RefreshToken.for_user(self.user)  # the person is still signed in somewhere
        kept = self.session(age_days=60, ended_days_ago=refresh_lifetime.days)
        gone = self.session(age_days=90, ended_days_ago=tasks.keep_ended_sessions().days + 1)
        tasks.purge(now=self.now)
        self.assertTrue(ProductSession.objects.filter(pk=kept.pk).exists())
        self.assertFalse(ProductSession.objects.filter(pk=gone.pk).exists())

    def test_an_open_session_stays_while_its_person_holds_a_live_refresh_token(self):
        open_session = self.session(age_days=90)
        RefreshToken.for_user(self.user)
        tasks.purge(now=self.now)
        self.assertTrue(ProductSession.objects.filter(pk=open_session.pk).exists())

    def test_an_open_session_whose_person_holds_no_live_refresh_token_goes(self):
        lapsed = self.session(age_days=10)
        young = self.session(age_days=0)  # its sign-in may still be committing
        revoked = RefreshToken.for_user(self.user)
        revoked.blacklist()  # signed out
        result = tasks.purge(now=self.now)
        self.assertEqual(result["lapsed_sessions"], 1)
        self.assertFalse(ProductSession.objects.filter(pk=lapsed.pk).exists())
        self.assertTrue(ProductSession.objects.filter(pk=young.pk).exists())

    def test_the_task_is_the_purge(self):
        self.assertEqual(set(tasks.purge_workeasy360_records_task()), {
            "flows", "logout_jtis", "ended_sids", "ended_sessions", "lapsed_sessions",
        })
