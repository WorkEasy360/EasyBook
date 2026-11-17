"""Turn verified Workeasy360 claims into an EasyBook user, organization and
membership.

Who someone is comes from (issuer, sub); which organization they are working
in comes from `organization_id`. Both are already verified by the
workeasy360-sso connector (signature, issuer, audience, nonce, product,
organization binding), and Workeasy360 has already checked the membership and
the product entitlement before it issued the token, so this module decides
only how the result maps onto EasyBook's own rows.

The connector calls it through ``UserAdapter`` and ``OrganizationAdapter``
(named in ``settings.WORKEASY360_SSO``), both inside one database transaction:
a refused membership leaves no new account behind. A refusal is a
``SignInRefused`` carrying EasyBook's own code (sso/errors.py maps it back for
the BFF).

``ExternalIdentity`` stays the authoritative (issuer, sub) -> user link. The
connector's own ``FederatedIdentity``, which its back-channel session records
point at, is kept a mirror of it on every sign-in (and was back-filled by
migration ``sso.0002``).
"""

from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from workeasy360_sso.errors import SignInRefused
from workeasy360_sso.models import FederatedIdentity
from workeasy360_sso.organizations import DefaultOrganizationAdapter
from workeasy360_sso.provisioning import DefaultUserAdapter

from accounts.models import Currency, Membership, Organization, User
from audit.models import AuditLog
from audit.services import record
from authz.roles import Role
from core.tenancy import tenant_context
from sso import entitlement
from sso.models import ExternalIdentity

#: Shown until the owner renames it, when the token carries no
#: organization_name (the integration was not granted the `organization`
#: scope).
FALLBACK_ORGANIZATION_NAME = "My organization"

#: Where ``UserAdapter`` leaves what it decided for ``OrganizationAdapter`` (the request EasyBook builds for
#: the connector, sso/flow.py): whether this sign-in linked an existing account.
_LINKED = "easybook_linked"


@dataclass(frozen=True)
class SignedIn:
    user: User
    organization: Organization
    membership: Membership
    organization_created: bool
    #: This sign-in attached the Workeasy360 account to an existing EasyBook account (by verified address).
    linked: bool


class UserAdapter(DefaultUserAdapter):
    """EasyBook's rules for who signs in, in place of the connector's default (issuer, sub) linking."""

    def resolve(self, request, *, claims):
        issuer = self.config.issuer
        user, linked = _user_for(claims, issuer=issuer)
        identity = sync_federated_identity(
            user, issuer=issuer, subject=str(claims["sub"]), email=str(claims.get("email") or "")
        )
        setattr(request, _LINKED, linked)
        # A new link changes which question the entitlement check asks for this person (sso/entitlement.py).
        # After commit, so no request can read the old answer from the cache once the new row is visible.
        transaction.on_commit(lambda: entitlement.forget_subject(user.pk))
        return user, identity


class OrganizationAdapter(DefaultOrganizationAdapter):
    """The EasyBook organization and membership for the token's organization. Returns ``SignedIn``, which the
    callback reads as the connector's ``SignInResult.binding``."""

    def bind(self, request, *, user, organization_id, claims):
        organization, created = _organization_for(claims)
        membership = _membership_for(user, organization, organization_created=created)
        return SignedIn(
            user=user,
            organization=organization,
            membership=membership,
            organization_created=created,
            linked=bool(getattr(request, _LINKED, False)),
        )


def record_sign_in(signed_in: SignedIn, claims: dict) -> None:
    """The audit trail of a completed sign-in. AuditLog.action is a closed verb set; object_type says what
    happened."""
    user, organization = signed_in.user, signed_in.organization
    changes = {"method": "workeasy360", "workeasy360_subject": claims["sub"], "aal": claims.get("aal")}
    if signed_in.linked:
        record(
            organization_id=organization.id, action=AuditLog.Action.CREATE, object_type="external_identity",
            object_id=user.id, actor=user, changes=changes,
        )
    if signed_in.organization_created:
        record(
            organization_id=organization.id, action=AuditLog.Action.CREATE, object_type="organization",
            object_id=organization.id, actor=user,
            changes={"source": "workeasy360", "workeasy360_organization_id": str(organization.workeasy360_organization_id)},
        )
    record(
        organization_id=organization.id, action=AuditLog.Action.CREATE, object_type="sign_in",
        object_id=user.id, actor=user, changes=changes,
    )


def sync_federated_identity(user: User, *, issuer: str, subject: str, email: str = "") -> FederatedIdentity:
    """The connector's ``FederatedIdentity`` for (issuer, subject), pointing at ``user``.

    ``ExternalIdentity`` decides who the person is; this mirror exists because the connector's back-channel
    session records point at it. A mirror row that disagrees (a link made by the previous release, or by the
    previous release again after a rollback) is corrected here, never the other way round.
    """
    now = timezone.now()
    # This user's mirror for another subject at the same issuer can only be stale: EasyBook allows one identity
    # per issuer and user, and ExternalIdentity has just said which one it is.
    FederatedIdentity.objects.filter(user=user, issuer=issuer).exclude(subject=subject).delete()
    identity, created = FederatedIdentity.objects.get_or_create(
        issuer=issuer,
        subject=subject,
        defaults={"user": user, "email_at_link": email[:254], "last_sign_in_at": now},
    )
    if not created:
        fields = ["last_sign_in_at"]
        if identity.user_id != user.pk:
            identity.user = user
            fields.append("user")
        identity.last_sign_in_at = now
        identity.save(update_fields=fields)
    return identity


def revoke_refresh_tokens(user: User) -> int:
    """Revoke every refresh token ``user`` holds (blacklisted, so it can neither refresh nor rotate). How many
    were still live."""
    live = OutstandingToken.objects.filter(user=user, expires_at__gt=timezone.now(), blacklistedtoken__isnull=True)
    revoked = 0
    for token in live:
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        revoked += int(created)
    return revoked


def _user_for(claims: dict, *, issuer: str) -> tuple[User, bool]:
    """(user, linked_now). Raises SignInRefused when the sign-in must not land."""
    subject = str(claims["sub"])
    identity = ExternalIdentity.objects.select_related("user").filter(issuer=issuer, subject=subject).first()
    if identity is not None:
        user = identity.user
        if not user.is_active:
            raise SignInRefused("account_disabled", log_reason="user_inactive")
        identity.last_login_at = timezone.now()
        identity.save(update_fields=["last_login_at", "updated_at"])
        return user, False

    email = str(claims.get("email") or "").strip()
    if not email:
        # Every EasyBook account has an address; the integration must be
        # granted the `email` scope.
        raise SignInRefused("email_required", log_reason="no_email_claim")

    if claims.get("email_verified") is not True:
        # An address the provider has not verified neither links an existing
        # account (whoever registered it at Workeasy360 unproven would take
        # the EasyBook account over) nor creates one (it would squat the
        # address its real owner later arrives with).
        raise SignInRefused("email_unverified", log_reason="address_not_verified")

    user = User.objects.filter(email__iexact=email).first()
    if user is not None:
        if not user.is_active:
            raise SignInRefused("account_disabled", log_reason="user_inactive")
        if ExternalIdentity.objects.filter(issuer=issuer, user=user).exists():
            # Already linked to a different Workeasy360 account.
            raise SignInRefused("account_conflict", log_reason="linked_to_other_subject")
        _retire_password(user)
        linked = True
    else:
        first_name, _, last_name = str(claims.get("name") or "").strip().partition(" ")
        # No password: this account can only be reached through Workeasy360.
        user = User.objects.create_user(email=email, password=None, first_name=first_name[:150],
                                        last_name=last_name.strip()[:150])
        linked = False

    try:
        with transaction.atomic():
            ExternalIdentity.objects.create(user=user, issuer=issuer, subject=subject, last_login_at=timezone.now())
    except IntegrityError:
        # The same person's first sign-in racing in another tab: whichever
        # won created the link, and it must name this user.
        identity = ExternalIdentity.objects.select_related("user").filter(issuer=issuer, subject=subject).first()
        if identity is None or identity.user_id != user.id:
            raise SignInRefused("account_conflict", log_reason="concurrent_link") from None
    return user, linked


def _retire_password(user: User) -> None:
    """EasyBook never verified anyone's address, so whoever chose this
    account's password may not be the person Workeasy360 just vouched for (an
    account pre-registered under someone else's address). From the link on,
    the account is reached through Workeasy360 only: the password goes, and
    every refresh token it issued is revoked."""
    user.set_unusable_password()
    user.save(update_fields=["password", "updated_at"])
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def _organization_for(claims: dict) -> tuple[Organization, bool]:
    external_id = str(claims["organization_id"])
    organization = Organization.objects.filter(workeasy360_organization_id=external_id).first()
    if organization is not None:
        if not organization.is_active:
            raise SignInRefused("organization_inactive", log_reason="organization_inactive")
        return organization, False

    currency = Currency.objects.filter(code=settings.WORKEASY360_DEFAULT_CURRENCY).first()
    if currency is None:
        raise SignInRefused("provider_misconfigured", log_reason="default_currency_not_seeded")
    name = str(claims.get("organization_name") or "").strip()[:255] or FALLBACK_ORGANIZATION_NAME
    try:
        with transaction.atomic():
            organization = Organization.objects.create(
                name=name, default_currency=currency, workeasy360_organization_id=external_id,
            )
    except IntegrityError:
        # Two people from the same new organization signing in at once: the
        # other request created it; join that one.
        return Organization.objects.get(workeasy360_organization_id=external_id), False
    return organization, True


def _membership_for(user: User, organization: Organization, *, organization_created: bool) -> Membership:
    with tenant_context(organization_id=organization.id, user_id=user.id):
        membership = Membership.all_objects.filter(organization=organization, user=user).first()
        if membership is not None:
            if not membership.is_active:
                # Removed by this organization's EasyBook admins. Workeasy360
                # granting product access does not override that.
                raise SignInRefused("membership_inactive", log_reason="membership_inactive")
            return membership
        role = Role.OWNER if organization_created else _default_role()
        try:
            with transaction.atomic():
                return Membership.all_objects.create(organization=organization, user=user, role=role)
        except IntegrityError:
            return Membership.all_objects.get(organization=organization, user=user)


def _default_role() -> str:
    role = settings.WORKEASY360_DEFAULT_ROLE
    if role not in Role.values or role == Role.OWNER:
        raise SignInRefused("provider_misconfigured", log_reason="default_role_not_assignable")
    return role
