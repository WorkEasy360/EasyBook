"""Turn verified Workeasy360 claims into an EasyBook user, organization and
membership.

Who someone is comes from (issuer, sub); which organization they are working
in comes from `organization_id`. Both are already verified (sso/oidc.py), and
Workeasy360 has already checked the membership and the product entitlement
before it issued the token, so this module decides only how the result maps
onto EasyBook's own rows.
"""

from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from accounts.models import Currency, Membership, Organization, User
from audit.models import AuditLog
from audit.services import record
from authz.roles import Role
from core.tenancy import tenant_context
from sso import entitlement
from sso.models import ExternalIdentity
from sso.oidc import SSOError

#: Shown until the owner renames it, when the token carries no
#: organization_name (the integration was not granted the `organization`
#: scope).
FALLBACK_ORGANIZATION_NAME = "My organization"


@dataclass(frozen=True)
class SignedIn:
    user: User
    organization: Organization
    membership: Membership
    organization_created: bool


def provision(claims: dict, *, issuer: str) -> SignedIn:
    with transaction.atomic():
        user, linked = _user_for(claims, issuer=issuer)
        organization, created = _organization_for(claims)
        membership = _membership_for(user, organization, organization_created=created)
    # A new link changes which question the entitlement check asks for this person (sso/entitlement.py).
    # After commit, so no request can read the old answer from the cache once the new row is visible.
    transaction.on_commit(lambda: entitlement.forget_subject(user.pk))

    # AuditLog.action is a closed verb set; object_type says what happened.
    changes = {"method": "workeasy360", "workeasy360_subject": claims["sub"], "aal": claims.get("aal")}
    if linked:
        record(
            organization_id=organization.id, action=AuditLog.Action.CREATE, object_type="external_identity",
            object_id=user.id, actor=user, changes=changes,
        )
    if created:
        record(
            organization_id=organization.id, action=AuditLog.Action.CREATE, object_type="organization",
            object_id=organization.id, actor=user,
            changes={"source": "workeasy360", "workeasy360_organization_id": str(organization.workeasy360_organization_id)},
        )
    record(
        organization_id=organization.id, action=AuditLog.Action.CREATE, object_type="sign_in",
        object_id=user.id, actor=user, changes=changes,
    )
    return SignedIn(user=user, organization=organization, membership=membership, organization_created=created)


def _user_for(claims: dict, *, issuer: str) -> tuple[User, bool]:
    """(user, linked_now). Raises SSOError when the sign-in must not land."""
    subject = str(claims["sub"])
    identity = ExternalIdentity.objects.select_related("user").filter(issuer=issuer, subject=subject).first()
    if identity is not None:
        user = identity.user
        if not user.is_active:
            raise SSOError("account_disabled", f"user {user.id} is inactive")
        identity.last_login_at = timezone.now()
        identity.save(update_fields=["last_login_at", "updated_at"])
        return user, False

    email = str(claims.get("email") or "").strip()
    if not email:
        # Every EasyBook account has an address; the integration must be
        # granted the `email` scope.
        raise SSOError("email_required", "the ID token carries no email claim")

    if claims.get("email_verified") is not True:
        # An address the provider has not verified neither links an existing
        # account (whoever registered it at Workeasy360 unproven would take
        # the EasyBook account over) nor creates one (it would squat the
        # address its real owner later arrives with).
        raise SSOError("email_unverified", "the provider has not verified the address")

    user = User.objects.filter(email__iexact=email).first()
    if user is not None:
        if not user.is_active:
            raise SSOError("account_disabled", f"user {user.id} is inactive")
        if ExternalIdentity.objects.filter(issuer=issuer, user=user).exists():
            # Already linked to a different Workeasy360 account.
            raise SSOError("account_conflict", f"user {user.id} is linked to another subject")
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
            raise SSOError("account_conflict", "a concurrent sign-in linked a different account") from None
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
            raise SSOError("organization_inactive", f"organization {organization.id} is inactive")
        return organization, False

    currency = Currency.objects.filter(code=settings.WORKEASY360_DEFAULT_CURRENCY).first()
    if currency is None:
        raise SSOError("provider_misconfigured", f"currency {settings.WORKEASY360_DEFAULT_CURRENCY!r} is not seeded")
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
                raise SSOError("membership_inactive", f"membership {membership.id} is inactive")
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
        raise SSOError("provider_misconfigured", f"WORKEASY360_DEFAULT_ROLE {role!r} is not a non-owner role")
    return role
