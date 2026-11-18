import uuid

from django.db import models

from accounts.models import User
from core.models import TimeStampedModel


class ExternalIdentity(TimeStampedModel):
    """A Workeasy360 account (issuer + subject) signed in as an EasyBook user.

    Keyed on the provider's `sub`, which is stable and opaque, never on the
    email address: an address can change at the provider, and keying on it
    would hand the account to whoever holds the address next. Global like
    `User` itself, so it carries no organization and needs no RLS.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="external_identities")
    issuer = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["issuer", "subject"], name="uniq_external_identity"),
            # One Workeasy360 account per user per issuer: a second subject
            # claiming the same user would be an account takeover.
            models.UniqueConstraint(fields=["issuer", "user"], name="uniq_external_identity_per_user"),
        ]

    def __str__(self):
        return f"{self.subject} @ {self.issuer}"
