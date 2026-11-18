"""Move EasyBook's Workeasy360 identity links to the configured issuer.

WorkEasy360's central sign-in is moving from ``https://auth.workeasy360.com`` to
``https://accounts.workeasy360.com`` (platform ADR-067): the same provider, keys and people (``sub``); only
``iss`` changes. EasyBook links people on (issuer, sub) twice - ``ExternalIdentity`` (authoritative) and the
connector's mirror (``FederatedIdentity``, which its back-channel session records point at) - so a release
that changes ``WORKEASY360_ISSUER`` must move both, or a returning person is not recognised (a second, empty
account, or ``account_conflict``). Run it in that release, before it takes traffic::

    python manage.py move_workeasy360_issuer --from https://auth.workeasy360.com          # dry run
    python manage.py move_workeasy360_issuer --from https://auth.workeasy360.com --apply  # move

One transaction for both tables; to the configured issuer only; idempotent. A duplicate link of one person on
one account (they signed in under both names) is folded. One person on two EasyBook accounts, or one account
holding two people, stops everything and is listed for a human decision - never merged. Rollback: put the
setting back and run it with ``--from https://accounts.workeasy360.com``.
"""

import logging

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from workeasy360_sso.conf import url_problem

from sso.config import issuer as configured_issuer
from sso.models import ExternalIdentity

logger = logging.getLogger(__name__)

#: How many conflicts to print; the count is always exact.
SHOW_AT_MOST = 50


class Command(BaseCommand):
    help = "Move Workeasy360 identity links (ExternalIdentity and the connector's mirror) to WORKEASY360_ISSUER."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from", dest="previous", required=True, help="The previous issuer, exactly as it was configured."
        )
        parser.add_argument("--apply", action="store_true", help="Make the change. Without it, only report.")

    def handle(self, *args, **options):
        target = configured_issuer()
        previous = str(options["previous"] or "").strip()
        problem = url_problem(previous, allow_insecure_http=bool(settings.WORKEASY360_ALLOW_INSECURE_HTTP))
        if problem:
            raise CommandError(f"--from {problem}.")
        if previous.endswith("/"):
            raise CommandError("--from must be written exactly as configured, without a trailing '/'.")
        if not target:
            raise CommandError("WORKEASY360_ISSUER is not configured.")
        if previous == target:
            raise CommandError(f"--from is already the configured issuer ({target}); nothing to move.")

        with transaction.atomic():
            moving = list(
                ExternalIdentity.objects.select_for_update().filter(issuer=previous).values_list("pk", flat=True)
            )
            duplicates, conflicts = _overlap(previous, target)
            self.stdout.write(f"EasyBook identities (ExternalIdentity): from {previous} to {target}")
            self.stdout.write(f"identities={len(moving)} duplicate_links={len(duplicates)} conflicts={len(conflicts)}")
            for line in conflicts[:SHOW_AT_MOST]:
                self.stdout.write(f"  {line}")
            if len(conflicts) > SHOW_AT_MOST:
                self.stdout.write(f"  ... and {len(conflicts) - SHOW_AT_MOST} more")
            self.stdout.write("Connector mirror (workeasy360_sso):")
            if not options["apply"]:
                call_command("w360sso_move_issuer", "--from", previous, stdout=self.stdout)
                self.stdout.write("dry run: nothing changed (add --apply to move)")
                return
            if conflicts:
                raise CommandError("Nothing moved: resolve the EasyBook conflicts above first.")
            if duplicates:
                ExternalIdentity.objects.filter(pk__in=duplicates).delete()
            moved = ExternalIdentity.objects.filter(issuer=previous).update(issuer=target)
            # Same transaction: a conflict in the mirror (CommandError) undoes the move above.
            call_command("w360sso_move_issuer", "--from", previous, "--apply", stdout=self.stdout)
        logger.info(
            "sso.issuer_moved",
            extra={"from": previous, "to": target, "identities": moved, "folded_duplicate_links": len(duplicates)},
        )
        self.stdout.write(self.style.SUCCESS(f"Moved {moved} EasyBook identities to {target}."))


def _overlap(previous: str, target: str) -> tuple[list, list[str]]:
    """``duplicates``: old-issuer rows of a person already linked to the same EasyBook user under the new
    issuer (safe to drop). ``conflicts``: one person on two EasyBook users, or one user holding two people."""
    old = ExternalIdentity.objects.filter(issuer=previous)
    new = ExternalIdentity.objects.filter(issuer=target)
    old_by_subject = {
        row["subject"]: row for row in old.filter(subject__in=new.values("subject")).values("pk", "subject", "user_id")
    }
    duplicates: list = []
    conflicts: list[str] = []
    folded: set[str] = set()
    for row in new.filter(subject__in=list(old_by_subject)).values("subject", "user_id"):
        before = old_by_subject[row["subject"]]
        if before["user_id"] == row["user_id"]:
            duplicates.append(before["pk"])
            folded.add(row["subject"])
        else:
            conflicts.append(
                f"sub={row['subject']} is two EasyBook users: "
                f"{before['user_id']} (old link) and {row['user_id']} (new link)"
            )
    old_by_user = {
        row["user_id"]: row["subject"] for row in old.exclude(subject__in=folded).values("user_id", "subject")
    }
    for row in new.filter(user_id__in=list(old_by_user)).exclude(subject__in=folded).values("user_id", "subject"):
        if old_by_user[row["user_id"]] != row["subject"]:
            conflicts.append(
                f"EasyBook user {row['user_id']} holds two people: sub={old_by_user[row['user_id']]} (old link) "
                f"and sub={row['subject']} (new link)"
            )
    return duplicates, sorted(conflicts)
