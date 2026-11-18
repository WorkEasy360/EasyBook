"""``move_workeasy360_issuer``: EasyBook's identity links follow WorkEasy360's move to accounts.workeasy360.com.

EasyBook links people on (issuer, sub) twice - ``ExternalIdentity`` (authoritative) and the connector's mirror -
so both move together, in one transaction, or neither does. Proven here on the real tables, and end to end: the
same person signs in to the same account after the move."""

import io
import uuid

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from workeasy360_sso.models import EndedProviderSession, FederatedIdentity, ProductSession

from accounts.models import User
from core.tests.factories import make_user
from sso.models import ExternalIdentity
from sso.tests.fakes import ISSUER
from sso.tests.test_sign_in import SignInTestCase

OLD = "https://auth.workeasy360.test"
NEW = "https://accounts.workeasy360.test"
#: The name the end-to-end tests' links were made under; the fake provider answers as ``ISSUER``.
PREVIOUS = "https://app.workeasy360.test"


@override_settings(WORKEASY360_ISSUER=NEW)
class MoveIssuerTests(SignInTestCase):
    def link(self, user, issuer, subject):
        """Both links, as an EasyBook sign-in leaves them."""
        ExternalIdentity.objects.create(user=user, issuer=issuer, subject=subject)
        FederatedIdentity.objects.create(user=user, issuer=issuer, subject=subject)

    def run_command(self, *args):
        out = io.StringIO()
        call_command("move_workeasy360_issuer", *args, stdout=out)
        return out.getvalue()

    def issuers(self, model, subject):
        return sorted(model.objects.filter(subject=subject).values_list("issuer", flat=True))

    def test_a_dry_run_reports_both_tables_and_changes_nothing(self):
        self.link(make_user(email="a@example.com"), OLD, "sub-a")
        out = self.run_command("--from", OLD)
        self.assertIn("EasyBook identities", out)
        self.assertIn("identities=1", out)
        self.assertIn("Connector mirror", out)
        self.assertIn("dry run", out)
        self.assertEqual(self.issuers(ExternalIdentity, "sub-a"), [OLD])
        self.assertEqual(self.issuers(FederatedIdentity, "sub-a"), [OLD])

    def test_apply_moves_both_links_with_their_sessions_and_is_idempotent(self):
        user = make_user(email="a@example.com")
        self.link(user, OLD, "sub-a")
        session = ProductSession.objects.create(
            identity=FederatedIdentity.objects.get(subject="sub-a"), session_key="k1", organization_id=uuid.uuid4()
        )
        EndedProviderSession.objects.create(issuer=OLD, provider_sid="sid-ended")

        self.assertIn("Moved 1 EasyBook identities", self.run_command("--from", OLD, "--apply"))
        self.assertEqual(self.issuers(ExternalIdentity, "sub-a"), [NEW])
        self.assertEqual(self.issuers(FederatedIdentity, "sub-a"), [NEW])
        session.refresh_from_db()
        self.assertEqual(session.identity.issuer, NEW)  # back-channel logout still finds it
        self.assertEqual(EndedProviderSession.objects.get(provider_sid="sid-ended").issuer, NEW)

        self.assertIn("Moved 0 EasyBook identities", self.run_command("--from", OLD, "--apply"))
        self.assertEqual(ExternalIdentity.objects.count(), 1)

    def test_a_duplicate_link_of_the_same_person_and_account_is_folded(self):
        user = make_user(email="a@example.com")
        self.link(user, OLD, "sub-a")
        self.link(user, NEW, "sub-a")
        self.run_command("--from", OLD, "--apply")
        self.assertEqual(self.issuers(ExternalIdentity, "sub-a"), [NEW])
        self.assertEqual(self.issuers(FederatedIdentity, "sub-a"), [NEW])

    def test_one_person_on_two_easybook_accounts_moves_nothing(self):
        self.link(make_user(email="a@example.com"), OLD, "sub-a")
        self.link(make_user(email="b@example.com"), NEW, "sub-a")
        self.link(make_user(email="c@example.com"), OLD, "sub-c")
        self.assertIn("two EasyBook users", self.run_command("--from", OLD))
        with self.assertRaisesMessage(CommandError, "Nothing moved"):
            self.run_command("--from", OLD, "--apply")
        self.assertEqual(self.issuers(ExternalIdentity, "sub-c"), [OLD])
        self.assertEqual(self.issuers(FederatedIdentity, "sub-c"), [OLD])

    def test_one_account_holding_two_people_moves_nothing(self):
        user = make_user(email="a@example.com")
        self.link(user, OLD, "sub-a")
        ExternalIdentity.objects.create(user=user, issuer=NEW, subject="sub-b")
        self.assertIn("holds two people", self.run_command("--from", OLD))
        with self.assertRaises(CommandError):
            self.run_command("--from", OLD, "--apply")
        self.assertEqual(self.issuers(ExternalIdentity, "sub-a"), [OLD])

    def test_a_mirror_conflict_undoes_the_easybook_move_too(self):
        """EasyBook's table is clean but the mirror is not: the whole move is rolled back."""
        self.link(make_user(email="a@example.com"), OLD, "sub-a")
        FederatedIdentity.objects.create(user=make_user(email="b@example.com"), issuer=NEW, subject="sub-a")
        with self.assertRaises(CommandError):
            self.run_command("--from", OLD, "--apply")
        self.assertEqual(self.issuers(ExternalIdentity, "sub-a"), [OLD])

    def test_it_refuses_a_source_it_cannot_trust(self):
        for previous, message in (
            (NEW, "already the configured issuer"),
            (f"{OLD}/", "trailing"),
            ("http://auth.workeasy360.test", "https"),
            ("", "empty"),
        ):
            with self.subTest(previous), self.assertRaisesMessage(CommandError, message):
                self.run_command("--from", previous)

    def test_rollback_is_the_same_command_the_other_way_round(self):
        self.link(make_user(email="a@example.com"), OLD, "sub-a")
        self.run_command("--from", OLD, "--apply")
        with override_settings(WORKEASY360_ISSUER=OLD):
            self.run_command("--from", NEW, "--apply")
        self.assertEqual(self.issuers(ExternalIdentity, "sub-a"), [OLD])
        self.assertEqual(self.issuers(FederatedIdentity, "sub-a"), [OLD])


class SignInAfterTheMoveTests(SignInTestCase):
    """End to end: a person linked under the previous issuer signs in under the new one."""

    def test_the_same_person_lands_on_the_same_account(self):
        # Linked under the previous name; the fake provider (and the configuration) now answer as ISSUER.
        user = make_user(email="asha@example.com")
        ExternalIdentity.objects.create(user=user, issuer=PREVIOUS, subject="w360-user-1")
        FederatedIdentity.objects.create(user=user, issuer=PREVIOUS, subject="w360-user-1")
        call_command("move_workeasy360_issuer", "--from", PREVIOUS, "--apply", stdout=io.StringIO())
        self.assertEqual(ExternalIdentity.objects.get().issuer, ISSUER)

        response = self.sign_in(email="asha.renamed@example.com")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(User.objects.count(), 1)  # no second, empty account
        self.assertEqual(ExternalIdentity.objects.get().user_id, user.id)
        self.assertEqual(FederatedIdentity.objects.get().user_id, user.id)

    def test_without_the_move_the_person_is_not_recognised(self):
        """Why the command exists: the links are on (issuer, sub). A person whose address changed since gets a
        second, empty account."""
        user = make_user(email="asha@example.com")
        ExternalIdentity.objects.create(user=user, issuer=PREVIOUS, subject="w360-user-1")
        response = self.sign_in(email="asha.renamed@example.com")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(User.objects.count(), 2)
