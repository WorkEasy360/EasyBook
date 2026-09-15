from accounts.models import Currency, Membership, Organization, User
from authz.roles import Role
from core.tenancy import tenant_context


def make_currency(code="INR"):
    currency, _ = Currency.objects.get_or_create(
        code=code, defaults={"name": code, "symbol": code, "decimal_places": 2}
    )
    return currency


def make_user(email="user@example.com", password="strongpassword123"):
    return User.objects.create_user(email=email, password=password)


def make_organization(name="Acme", currency=None):
    currency = currency or make_currency()
    return Organization.objects.create(name=name, default_currency=currency)


def make_membership(organization, user, role=Role.OWNER):
    # The Membership RLS policy admits a row when it belongs to the current
    # org OR the current user (see accounts/migrations/0002_enable_rls.py);
    # satisfy it via user_id since no org context is selected yet here.
    with tenant_context(user_id=user.id):
        return Membership.objects.create(organization=organization, user=user, role=role)


def make_org_with_owner(org_name="Acme", email="owner@example.com", password="strongpassword123"):
    user = make_user(email=email, password=password)
    organization = make_organization(name=org_name)
    membership = make_membership(organization, user, role=Role.OWNER)
    return organization, user, membership
