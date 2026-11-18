"""Attach an existing EasyBook organization to its Workeasy360 organization.

    manage.py link_workeasy360_organization <easybook-organization-id> <workeasy360-organization-id>

Without this, the first Workeasy360 sign-in for a Workeasy360 organization
provisions a new, empty EasyBook organization (sso/provisioning.py). Run it
before that first sign-in when the organization's books already live here.
Refuses when either side is already linked: re-pointing a link would hand one
organization's books to another's members.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Organization
from audit.models import AuditLog
from audit.services import record


class Command(BaseCommand):
    help = "Link an EasyBook organization to a Workeasy360 organization (Workeasy360 sign-in)."

    def add_arguments(self, parser):
        parser.add_argument("organization", help="EasyBook organization id.")
        parser.add_argument("workeasy360_organization", help="Workeasy360 organization id (the launcher's ?organization=).")

    def handle(self, *args, organization, workeasy360_organization, **options):
        try:
            external_id = uuid.UUID(workeasy360_organization)
            organization_id = uuid.UUID(organization)
        except ValueError:
            raise CommandError("Both ids must be UUIDs.") from None

        with transaction.atomic():
            target = Organization.objects.select_for_update().filter(pk=organization_id).first()
            if target is None:
                raise CommandError(f"No EasyBook organization {organization_id}.")
            if target.workeasy360_organization_id is not None:
                raise CommandError(
                    f"{target.name} is already linked to Workeasy360 organization {target.workeasy360_organization_id}."
                )
            holder = Organization.objects.filter(workeasy360_organization_id=external_id).first()
            if holder is not None:
                raise CommandError(f"Workeasy360 organization {external_id} is already linked to {holder.name} ({holder.id}).")
            target.workeasy360_organization_id = external_id
            target.save(update_fields=["workeasy360_organization_id", "updated_at"])
            record(
                organization_id=target.id, action=AuditLog.Action.UPDATE, object_type="organization",
                object_id=target.id, changes={"workeasy360_organization_id": str(external_id), "source": "manage.py"},
            )
        self.stdout.write(self.style.SUCCESS(f"Linked {target.name} ({target.id}) to Workeasy360 organization {external_id}."))
