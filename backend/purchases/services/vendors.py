from django.db import transaction

from accounting.services.currency import assert_base_currency
from accounts.models import Currency
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from purchases.models.vendor import Vendor
from tax.enums import TaxTreatment
from tax.services.party import clean_party_tax_fields


def _validate_currency(*, currency: Currency | None) -> Currency:
    # Currency is global reference data (accounts.Currency), not org-scoped —
    # nothing to cross-org validate, unlike accounting.Account FKs.
    if currency is None:
        raise ApplicationError("currency is required.", code="currency_required")
    return currency


def _validate_default_payable_account(*, organization, account) -> None:
    if account is None:
        return
    from accounting.models.account import AccountType

    if account.organization_id != organization.id:
        raise ApplicationError(
            "default_payable_account must belong to the posting organization.", code="cross_org_reference"
        )
    if account.account_type != AccountType.LIABILITY:
        raise ApplicationError(
            "default_payable_account must reference a liability account.", code="invalid_account_type"
        )


def _assert_vendor_code_unused(*, organization, vendor_code: str, exclude_vendor=None) -> None:
    """A clear domain error instead of the IntegrityError that
    `uniq_vendor_code_per_org` raises, which surfaced as a 500 on an ordinary
    typo'd or re-used code. The constraint stays the real backstop against a
    concurrent insert; this is the same belt-and-braces split as
    bills._assert_vendor_bill_number_unused."""
    qs = Vendor.objects.filter(organization=organization, vendor_code=vendor_code)
    if exclude_vendor is not None:
        qs = qs.exclude(pk=exclude_vendor.pk)
    if qs.exists():
        raise ApplicationError(
            f"A vendor with code '{vendor_code}' already exists.", code="duplicate_vendor_code"
        )


@transaction.atomic
def create_vendor(
    *,
    organization,
    vendor_code: str,
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
    default_payable_account=None,
    notes: str = "",
    # Same defect as create_customer: VendorSerializer marks is_active
    # writable, so sending it on create raised TypeError -> 500.
    is_active: bool = True,
    actor=None,
) -> Vendor:
    _validate_currency(currency=currency)
    assert_base_currency(organization=organization, currency=currency)
    _validate_default_payable_account(organization=organization, account=default_payable_account)
    _assert_vendor_code_unused(organization=organization, vendor_code=vendor_code)

    tax_fields = clean_party_tax_fields(
        {"gstin": gstin, "pan": pan, "tax_treatment": tax_treatment},
        state_code=place_of_supply_state_code,
    )

    vendor = Vendor.objects.create(
        organization=organization,
        vendor_code=vendor_code,
        display_name=display_name,
        legal_name=legal_name,
        email=email,
        phone=phone,
        billing_address=billing_address or {},
        shipping_address=shipping_address or {},
        currency=currency,
        payment_terms_days=payment_terms_days,
        default_payable_account=default_payable_account,
        notes=notes,
        is_active=is_active,
        **tax_fields,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.Vendor",
        object_id=vendor.id,
        changes={"vendor_code": vendor_code, "display_name": display_name},
    )
    return vendor


@transaction.atomic
def update_vendor(*, vendor: Vendor, actor=None, **fields) -> Vendor:
    if "currency" in fields:
        _validate_currency(currency=fields["currency"])
        assert_base_currency(organization=vendor.organization, currency=fields["currency"])
    if "default_payable_account" in fields:
        _validate_default_payable_account(
            organization=vendor.organization, account=fields["default_payable_account"]
        )
    if "vendor_code" in fields and fields["vendor_code"] != vendor.vendor_code:
        _assert_vendor_code_unused(
            organization=vendor.organization, vendor_code=fields["vendor_code"], exclude_vendor=vendor
        )

    # Only the tax keys actually supplied are cleaned and written back, so a
    # partial update cannot blank a field the caller never mentioned.
    tax_keys = {k: v for k, v in fields.items() if k in {"gstin", "pan", "tax_treatment"}}
    if tax_keys or "place_of_supply_state_code" in fields:
        state_code = fields.pop("place_of_supply_state_code", None)
        merged = {"gstin": vendor.gstin, "tax_treatment": vendor.tax_treatment}
        merged.update(tax_keys)
        cleaned = clean_party_tax_fields(merged, state_code=state_code)
        fields.update({k: v for k, v in cleaned.items() if k in tax_keys or k == "place_of_supply_state"})

    changes = {}
    for field, value in fields.items():
        if getattr(vendor, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(vendor, field, value)
    if not changes:
        return vendor

    vendor.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=vendor.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.Vendor",
        object_id=vendor.id,
        changes=changes,
    )
    return vendor


def archive_vendor(*, vendor: Vendor, actor=None) -> Vendor:
    return update_vendor(vendor=vendor, is_active=False, actor=actor)


def assert_vendor_usable_for_new_transaction(*, vendor: Vendor) -> None:
    """The shared guard every purchase service must call before attaching a
    vendor to a NEW document. An inactive vendor cannot be used going
    forward, but existing historical documents referencing it stay fully
    readable (`on_delete=PROTECT` everywhere) — mirrors
    sales.services.customers.assert_customer_usable_for_new_transaction.
    """
    if not vendor.is_active:
        raise ApplicationError(
            "Inactive vendors cannot be used in new transactions.", code="vendor_inactive"
        )
