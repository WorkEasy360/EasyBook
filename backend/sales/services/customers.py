from django.db import transaction

from accounting.services.currency import assert_base_currency
from accounts.models import Currency
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from sales.models.customer import Customer
from tax.enums import TaxTreatment
from tax.services.party import clean_party_tax_fields


def _validate_currency(*, currency: Currency | None) -> Currency:
    # Currency is global reference data (accounts.Currency), not org-scoped —
    # nothing to cross-org validate, unlike accounts.Account/items.Item FKs.
    if currency is None:
        raise ApplicationError("currency is required.", code="currency_required")
    return currency


def _assert_customer_code_unused(*, organization, customer_code: str, exclude_customer=None) -> None:
    """A domain error instead of the IntegrityError `uniq_customer_code_per_org`
    raises, which surfaced as a 500 on an ordinary re-used code. The constraint
    remains the backstop against a concurrent insert (same split as
    purchases.services.vendors._assert_vendor_code_unused)."""
    qs = Customer.objects.filter(organization=organization, customer_code=customer_code)
    if exclude_customer is not None:
        qs = qs.exclude(pk=exclude_customer.pk)
    if qs.exists():
        raise ApplicationError(
            f"A customer with code '{customer_code}' already exists.", code="duplicate_customer_code"
        )


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
    tax_treatment: str = TaxTreatment.UNREGISTERED,
    place_of_supply_state_code=None,
    billing_address: dict | None = None,
    shipping_address: dict | None = None,
    payment_terms_days: int = 0,
    credit_limit=None,
    notes: str = "",
    # CustomerSerializer exposes is_active as writable, so a client that sends
    # it on create reached this signature and raised TypeError -> 500. Accepted
    # here rather than made read-only on the serializer because update_customer
    # already honours it, and an import of historical parties legitimately
    # creates one inactive.
    is_active: bool = True,
    actor=None,
) -> Customer:
    _validate_currency(currency=currency)
    assert_base_currency(organization=organization, currency=currency)
    if credit_limit is not None and credit_limit < 0:
        raise ApplicationError("credit_limit cannot be negative.", code="credit_limit_invalid")
    _assert_customer_code_unused(organization=organization, customer_code=customer_code)

    tax_fields = clean_party_tax_fields(
        {"gstin": gstin, "pan": pan, "tax_treatment": tax_treatment},
        state_code=place_of_supply_state_code,
    )

    customer = Customer.objects.create(
        organization=organization,
        customer_code=customer_code,
        display_name=display_name,
        legal_name=legal_name,
        email=email,
        phone=phone,
        billing_address=billing_address or {},
        shipping_address=shipping_address or {},
        currency=currency,
        payment_terms_days=payment_terms_days,
        credit_limit=credit_limit,
        notes=notes,
        is_active=is_active,
        **tax_fields,
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
        assert_base_currency(organization=customer.organization, currency=fields["currency"])
    if "credit_limit" in fields and fields["credit_limit"] is not None and fields["credit_limit"] < 0:
        raise ApplicationError("credit_limit cannot be negative.", code="credit_limit_invalid")
    if "customer_code" in fields and fields["customer_code"] != customer.customer_code:
        _assert_customer_code_unused(
            organization=customer.organization, customer_code=fields["customer_code"], exclude_customer=customer
        )

    # Only the tax keys actually supplied are cleaned and written back, so a
    # partial update cannot blank a field the caller never mentioned.
    tax_keys = {k: v for k, v in fields.items() if k in {"gstin", "pan", "tax_treatment"}}
    if tax_keys or "place_of_supply_state_code" in fields:
        state_code = fields.pop("place_of_supply_state_code", None)
        merged = {"gstin": customer.gstin, "tax_treatment": customer.tax_treatment}
        merged.update(tax_keys)
        cleaned = clean_party_tax_fields(merged, state_code=state_code)
        fields.update({k: v for k, v in cleaned.items() if k in tax_keys or k == "place_of_supply_state"})

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
