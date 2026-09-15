from django.db import transaction

from accounts.models import Currency
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from sales.models.customer import Customer


def _validate_currency(*, currency: Currency | None) -> Currency:
    # Currency is global reference data (accounts.Currency), not org-scoped —
    # nothing to cross-org validate, unlike accounts.Account/items.Item FKs.
    if currency is None:
        raise ApplicationError("currency is required.", code="currency_required")
    return currency


@transaction.atomic
def create_customer(
    *,
    organization,
    customer_code: str,
    display_name: str,
    currency: Currency,
    legal_name: str = "",
    email: str = "",
    phone: str = "",
    gstin: str = "",
    pan: str = "",
    billing_address: dict | None = None,
    shipping_address: dict | None = None,
    payment_terms_days: int = 0,
    credit_limit=None,
    notes: str = "",
    actor=None,
) -> Customer:
    _validate_currency(currency=currency)
    if credit_limit is not None and credit_limit < 0:
        raise ApplicationError("credit_limit cannot be negative.", code="credit_limit_invalid")

    customer = Customer.objects.create(
        organization=organization,
        customer_code=customer_code,
        display_name=display_name,
        legal_name=legal_name,
        email=email,
        phone=phone,
        gstin=gstin,
        pan=pan,
        billing_address=billing_address or {},
        shipping_address=shipping_address or {},
        currency=currency,
        payment_terms_days=payment_terms_days,
        credit_limit=credit_limit,
        notes=notes,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.Customer",
        object_id=customer.id,
        changes={"customer_code": customer_code, "display_name": display_name},
    )
    return customer


@transaction.atomic
def update_customer(*, customer: Customer, actor=None, **fields) -> Customer:
    if "currency" in fields:
        _validate_currency(currency=fields["currency"])
    if "credit_limit" in fields and fields["credit_limit"] is not None and fields["credit_limit"] < 0:
        raise ApplicationError("credit_limit cannot be negative.", code="credit_limit_invalid")

    changes = {}
    for field, value in fields.items():
        if getattr(customer, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(customer, field, value)
    if not changes:
        return customer

    customer.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=customer.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.Customer",
        object_id=customer.id,
        changes=changes,
    )
    return customer


def archive_customer(*, customer: Customer, actor=None) -> Customer:
    return update_customer(customer=customer, is_active=False, actor=actor)


def assert_customer_usable_for_new_transaction(*, customer: Customer) -> None:
    """Shared guard for quotes/orders/invoices (later slices): an inactive
    customer cannot be attached to a NEW transaction, but existing historical
    transactions referencing it remain untouched and readable."""
    if not customer.is_active:
        raise ApplicationError(
            "Inactive customers cannot be used in new transactions.", code="customer_inactive"
        )
