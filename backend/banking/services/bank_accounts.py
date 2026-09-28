"""Bank and credit card account setup."""

import re

from django.db import transaction

from accounting.models.account import AccountType
from accounting.services.currency import assert_base_currency
from audit.models import AuditLog
from audit.services import record as record_audit
from banking.models.bank_account import BankAccount, BankAccountKind
from banking.models.statement import BankTransaction
from banking.providers.base import get_provider
from core.exceptions import ApplicationError

# The GL account type each kind of bank account must be linked to. A credit
# card is money OWED, so its ledger account is a liability; pairing a card
# with an asset account would make every balance sheet overstate cash by the
# card balance, twice over.
_REQUIRED_ACCOUNT_TYPE = {
    BankAccountKind.BANK: AccountType.ASSET,
    BankAccountKind.CREDIT_CARD: AccountType.LIABILITY,
}

_DIGITS = re.compile(r"\D")


def _last4(value: str) -> str:
    """Keep only the final four digits of whatever the user typed.

    Truncating here rather than validating-and-storing is deliberate: a user
    who pastes a full account or card number must not end up with it
    persisted, and rejecting the paste would just move the full number into
    an error message and from there into the logs.
    """
    digits = _DIGITS.sub("", value or "")
    return digits[-4:]


def _assert_name_available(*, organization_id, name: str, exclude_id=None) -> None:
    """uniq_bank_account_name_per_org, checked before the insert so a reused
    name is a 400 with a sentence rather than an IntegrityError and a 500."""
    duplicates = BankAccount.objects.filter(organization_id=organization_id, name=name)
    if exclude_id is not None:
        duplicates = duplicates.exclude(pk=exclude_id)
    if duplicates.exists():
        raise ApplicationError(
            "A bank account with this name already exists.", code="bank_account_name_taken"
        )


@transaction.atomic
def create_bank_account(
    *,
    organization,
    name: str,
    account,
    currency=None,
    kind: str = BankAccountKind.BANK,
    bank_name: str = "",
    account_number: str = "",
    branch_identifier: str = "",
    opening_balance=None,
    opening_balance_date=None,
    provider_key: str = "manual",
    notes: str = "",
    actor=None,
) -> BankAccount:
    assert_base_currency(organization=organization, currency=currency)
    if account.organization_id != organization.id:
        raise ApplicationError(
            "The ledger account must belong to this organization.", code="cross_org_reference"
        )
    required_type = _REQUIRED_ACCOUNT_TYPE[kind]
    if account.account_type != required_type:
        raise ApplicationError(
            f"A {kind.replace('_', ' ')} must be linked to an account of type '{required_type}'.",
            code="invalid_account_type",
        )
    if BankAccount.objects.filter(account=account).exists():
        raise ApplicationError(
            "That ledger account is already linked to another bank account.",
            code="account_already_linked",
        )
    _assert_name_available(organization_id=organization.id, name=name)
    if get_provider(provider_key) is None:
        raise ApplicationError(f"Unknown bank feed provider '{provider_key}'.", code="provider_unknown")
    if opening_balance is not None and opening_balance != 0 and opening_balance_date is None:
        raise ApplicationError(
            "An opening statement balance needs the date it was the balance ON.",
            code="opening_balance_date_required",
        )

    bank_account = BankAccount.objects.create(
        organization=organization,
        kind=kind,
        name=name,
        account=account,
        currency=currency or organization.default_currency,
        bank_name=bank_name,
        account_number_last4=_last4(account_number),
        branch_identifier=branch_identifier,
        opening_balance=opening_balance if opening_balance is not None else 0,
        opening_balance_date=opening_balance_date,
        provider_key=provider_key,
        notes=notes,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="banking.BankAccount",
        object_id=bank_account.id,
        # The masked digits are not recorded: an audit trail is read far more
        # widely than the record it describes.
        changes={"name": name, "kind": kind, "account": account.code},
    )
    return bank_account


@transaction.atomic
def update_bank_account(*, bank_account: BankAccount, actor=None, **fields) -> BankAccount:
    """Descriptive fields only.

    `account` and `kind` are absent by design. Re-pointing a bank account at a
    different ledger account would orphan every reconciliation already closed
    against the old one, and changing kind would flip the required account
    type underneath it. Both are "make a new bank account" operations.
    """
    if "account_number" in fields:
        fields["account_number_last4"] = _last4(fields.pop("account_number"))

    allowed = {
        "name",
        "bank_name",
        "branch_identifier",
        "notes",
        "is_active",
        "opening_balance",
        "opening_balance_date",
        "provider_key",
        "account_number_last4",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ApplicationError(
            f"These fields cannot be changed after creation: {sorted(unknown)}.",
            code="bank_account_field_immutable",
        )

    if "name" in fields:
        _assert_name_available(
            organization_id=bank_account.organization_id, name=fields["name"], exclude_id=bank_account.pk
        )
    if "provider_key" in fields and get_provider(fields["provider_key"]) is None:
        raise ApplicationError(
            f"Unknown bank feed provider '{fields['provider_key']}'.", code="provider_unknown"
        )
    if ("opening_balance" in fields or "opening_balance_date" in fields) and BankTransaction.objects.filter(
        bank_account=bank_account
    ).exists():
        # Every statement and cleared balance is measured from this anchor.
        # Moving it after lines exist retrospectively changes balances that
        # may already have been reconciled and signed off.
        raise ApplicationError(
            "The opening balance cannot be changed once statement lines have been imported.",
            code="opening_balance_locked",
        )

    changes = {}
    for field, value in fields.items():
        if getattr(bank_account, field) == value:
            continue
        # Even the masked digits stay out of the audit trail, which is read
        # far more widely than the record it describes. Noting THAT it
        # changed is what an auditor needs; the value is on the account.
        changes[field] = "[redacted]" if field == "account_number_last4" else str(value)
        setattr(bank_account, field, value)
    if not changes:
        return bank_account

    bank_account.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=bank_account.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="banking.BankAccount",
        object_id=bank_account.id,
        changes=changes,
    )
    return bank_account
