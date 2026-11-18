"""Link an existing EasyBook account to its person's Workeasy360 account, vouched for by an operator.

    manage.py link_workeasy360_user <email-or-user-id> --subject <workeasy360-sub> \\
        --organization <workeasy360-organization-id> --actor "<who is running this>" --reason "<why>"

EasyBook never merges an existing account with a Workeasy360 account because the addresses match: that
sign-in is refused (``account_conflict``, sso/provisioning.py) unless ``WORKEASY360_LINK_BY_VERIFIED_EMAIL`` is
on. This is the deliberate step instead. The operator confirms, outside EasyBook, that the Workeasy360 account
(its ``sub``, from WorkEasy360 Accounts) and the EasyBook account belong to the same person, and names the
Workeasy360 organization the person works in here.

It binds (issuer, sub) to the account for the configured issuer (``--issuer`` to name it, which must be the
configured one), in ``ExternalIdentity`` and the connector's mirror, and ends the refresh tokens the account
issued before the link. The password is left as it is: SSO-only mode (``WORKEASY360_SSO_ONLY``) is what retires
password sign-in. One audit record (in the organization's audit log) names the operator, the reason, both
accounts and the account's other organizations, which the link opens too.

Refuses, writing nothing, when:
- the Workeasy360 organization is not linked to an EasyBook organization (``link_workeasy360_organization``);
- the account is not an active member of that organization, or is disabled;
- the account already holds a Workeasy360 identity (under any issuer);
- the Workeasy360 account (issuer, sub) is already linked to an EasyBook account.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone
from workeasy360_sso.models import FederatedIdentity

from accounts.models import Organization, User
from audit.models import AuditLog
from audit.services import record
from sso import entitlement
from sso.config import issuer as configured_issuer
from sso.models import ExternalIdentity
from sso.provisioning import LINK_OPERATOR, active_organization_ids, has_workeasy360_identity, revoke_refresh_tokens


class Command(BaseCommand):
    help = "Link an existing EasyBook account to a Workeasy360 account (issuer, sub), vouched for by an operator."

    def add_arguments(self, parser):
        parser.add_argument("user", help="The EasyBook account: its email address or its user id.")
        parser.add_argument("--subject", required=True, help="The person's Workeasy360 account id (the `sub`).")
        parser.add_argument(
            "--organization", required=True,
            help="The Workeasy360 organization id the person works in here (already linked to an EasyBook organization).",
        )
        parser.add_argument("--actor", required=True, help="Who is making this link (recorded in the audit log).")
        parser.add_argument("--reason", required=True, help="Why, e.g. the support ticket (recorded in the audit log).")
        parser.add_argument("--issuer", help="The Workeasy360 issuer. Default, and the only accepted value: WORKEASY360_ISSUER.")

    def handle(self, *args, user, subject, organization, actor, reason, issuer=None, **options):
        subject, actor, reason = str(subject).strip(), str(actor).strip(), str(reason).strip()
        if not subject:
            raise CommandError("--subject must not be empty.")
        if not actor or not reason:
            raise CommandError("--actor and --reason must not be empty: both go into the audit record.")
        target_issuer = configured_issuer()
        if not target_issuer:
            raise CommandError("WORKEASY360_ISSUER is not configured.")
        if issuer is not None and issuer != target_issuer:
            raise CommandError(f"--issuer must be the configured issuer ({target_issuer}): sign-ins verify only that one.")
        try:
            external_organization = uuid.UUID(str(organization))
        except ValueError:
            raise CommandError("--organization must be a Workeasy360 organization id (a UUID).") from None

        with transaction.atomic():
            account = self._account(user)
            here = Organization.objects.filter(workeasy360_organization_id=external_organization).first()
            if here is None:
                raise CommandError(
                    f"Workeasy360 organization {external_organization} is not linked to an EasyBook organization "
                    "(manage.py link_workeasy360_organization)."
                )
            if not account.is_active:
                raise CommandError(f"{account.email} is disabled.")
            memberships = active_organization_ids(account)
            if here.id not in memberships:
                raise CommandError(f"{account.email} is not an active member of {here.name} ({here.id}).")
            if has_workeasy360_identity(account):
                linked = ExternalIdentity.objects.filter(user=account).values_list("issuer", "subject").first()
                held = f" ({linked[1]} at {linked[0]})" if linked else ""
                raise CommandError(f"{account.email} is already linked to a Workeasy360 account{held}.")
            holder = (
                ExternalIdentity.objects.filter(issuer=target_issuer, subject=subject).select_related("user").first()
                or FederatedIdentity.objects.filter(issuer=target_issuer, subject=subject).select_related("user").first()
            )
            if holder is not None:
                raise CommandError(
                    f"Workeasy360 account {subject} is already linked to another EasyBook account ({holder.user.email})."
                )

            try:
                with transaction.atomic():
                    ExternalIdentity.objects.create(user=account, issuer=target_issuer, subject=subject)
                    FederatedIdentity.objects.create(
                        user=account, issuer=target_issuer, subject=subject, email_at_link=account.email[:254],
                        linked_at=timezone.now(),
                    )
            except IntegrityError:
                # A sign-in or another operator linked one side meanwhile.
                raise CommandError("Either account was linked meanwhile; nothing was changed.") from None
            revoked = revoke_refresh_tokens(account)
            others = sorted(str(organization_id) for organization_id in memberships - {here.id})
            record(
                organization_id=here.id, action=AuditLog.Action.CREATE, object_type="external_identity",
                object_id=account.id,
                changes={
                    "link": LINK_OPERATOR, "source": "manage.py link_workeasy360_user", "operator": actor,
                    "reason": reason, "issuer": target_issuer, "workeasy360_subject": subject,
                    "workeasy360_organization_id": str(external_organization), "email": account.email,
                    "other_organizations": others, "refresh_tokens_revoked": revoked,
                    "password_kept": account.has_usable_password(),
                },
            )
            # A new link changes which question the entitlement check asks for this person (sso/entitlement.py).
            transaction.on_commit(lambda: entitlement.forget_subject(account.pk))

        self.stdout.write(self.style.SUCCESS(
            f"Linked {account.email} ({account.id}) to Workeasy360 account {subject} at {target_issuer}, "
            f"in {here.name} ({here.id})."
        ))
        if others:
            self.stdout.write(f"The account also belongs to these EasyBook organizations, which the link opens too: {', '.join(others)}.")

    def _account(self, value) -> User:
        value = str(value).strip()
        try:
            account = User.objects.select_for_update().filter(pk=uuid.UUID(value)).first()
        except ValueError:
            account = User.objects.select_for_update().filter(email__iexact=value).first()
        if account is None:
            raise CommandError(f"No EasyBook account {value}.")
        return account
