"""Statement lines that did not arrive through an import, and the
exclude/restore decision.

EXCLUDE MEANS "THIS IS NOT A REAL TRANSACTION", AND NOTHING ELSE.

It is the single most abusable control in reconciliation software, because it
is the fastest way to make a stubborn difference disappear. So it is defined
narrowly here and the definition is enforced by the arithmetic rather than by
documentation: `selectors.get_statement_balance` leaves excluded lines out of
the statement balance entirely. That is correct ONLY for lines that are
artefacts — a duplicate the bank itself emitted, a header row a parser
mistook for data, a line belonging to a different entity. For those, the
bank's own closing balance does not include them either, so removing them
keeps both sides honest.

A line that IS real but has no counterpart in the books is the opposite case:
excluding it would hide a genuine difference and leave the ledger permanently
short. The answer there is `matching.categorize_transaction`, which posts the
journal the books were missing.
"""

from django.db import IntegrityError
from django.db import transaction as db_transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from banking.models.statement import BankTransaction, BankTransactionStatus, StatementFormat
from banking.services.imports import compute_fingerprint
from core.exceptions import ApplicationError


@db_transaction.atomic
def add_manual_transaction(
    *,
    organization,
    bank_account,
    transaction_date,
    amount,
    description: str = "",
    counterparty_name: str = "",
    bank_reference: str = "",
    external_id: str = "",
    actor=None,
) -> BankTransaction:
    """Record a statement line by hand.

    This is also the deliberate escape hatch from the import deduplicator. The
    multiset rule in services/imports.py cannot tell "the bank charged me the
    same amount on the same day again" from "this is an overlapping export",
    and no rule can. Rather than guess, the importer takes the conservative
    reading and a person adds the genuine repeat here — an act with an author
    and an audit record, which a silent import would not have.
    """
    if bank_account.organization_id != organization.id:
        raise ApplicationError(
            "Bank account must belong to this organization.", code="cross_org_reference"
        )
    if amount == 0:
        raise ApplicationError("A transaction amount cannot be zero.", code="transaction_amount_invalid")

    fingerprint = compute_fingerprint(
        transaction_date=transaction_date,
        amount=amount,
        description=description,
        bank_reference=bank_reference,
        external_id=external_id,
    )
    # Locked, so two people adding the same line concurrently cannot both
    # read the same occurrence count and both insert at that ordinal.
    existing = (
        BankTransaction.objects.select_for_update()
        .filter(bank_account=bank_account, fingerprint=fingerprint)
        .count()
    )
    try:
        created = BankTransaction.objects.create(
            organization=organization,
            bank_account=bank_account,
            statement_import=None,
            transaction_date=transaction_date,
            amount=amount,
            description=description,
            counterparty_name=counterparty_name,
            bank_reference=bank_reference,
            external_id=external_id,
            fingerprint=fingerprint,
            duplicate_ordinal=existing,
        )
    except IntegrityError:
        raise ApplicationError(
            "An identical transaction was added concurrently; retry.",
            code="transaction_occurrence_conflict",
        )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="banking.BankTransaction",
        object_id=created.id,
        changes={
            "bank_account": str(bank_account.id),
            "amount": str(amount),
            "transaction_date": str(transaction_date),
            "source": StatementFormat.MANUAL,
        },
    )
    return created


def _get_for_update(*, transaction_id, organization) -> BankTransaction:
    locked = (
        BankTransaction.objects.select_for_update()
        .filter(pk=transaction_id, organization=organization)
        .first()
    )
    if locked is None:
        raise ApplicationError("Bank transaction not found.", code="bank_transaction_not_found", status_code=404)
    return locked


@db_transaction.atomic
def exclude_transaction(*, transaction_id, organization, reason: str, actor=None) -> BankTransaction:
    """Mark a line as not a real transaction. A reason is mandatory."""
    if not (reason or "").strip():
        raise ApplicationError(
            "Excluding a transaction requires a reason.", code="exclusion_reason_required"
        )
    transaction = _get_for_update(transaction_id=transaction_id, organization=organization)
    if transaction.status == BankTransactionStatus.EXCLUDED:
        return transaction

    from banking.services.matching import _assert_open_for_changes

    _assert_open_for_changes(transaction)
    if transaction.matches.filter(is_confirmed=True).exists():
        # If it is matched to a document, it demonstrably IS real.
        raise ApplicationError(
            "Unmatch this transaction before excluding it.", code="cannot_exclude_matched_transaction"
        )

    # Suggestions against a line we have declared unreal are noise.
    transaction.matches.filter(is_confirmed=False).delete()
    transaction.status = BankTransactionStatus.EXCLUDED
    transaction.excluded_reason = reason.strip()[:255]
    transaction.save(update_fields=["status", "excluded_reason", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="banking.BankTransaction",
        object_id=transaction.id,
        changes={"status": BankTransactionStatus.EXCLUDED, "reason": transaction.excluded_reason},
    )
    return transaction


@db_transaction.atomic
def restore_transaction(*, transaction_id, organization, actor=None) -> BankTransaction:
    """Undo an exclusion, returning the line to the work queue."""
    transaction = _get_for_update(transaction_id=transaction_id, organization=organization)
    if transaction.status != BankTransactionStatus.EXCLUDED:
        return transaction

    from banking.services.matching import _assert_open_for_changes

    _assert_open_for_changes(transaction)
    transaction.status = BankTransactionStatus.UNMATCHED
    transaction.excluded_reason = ""
    transaction.save(update_fields=["status", "excluded_reason", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="banking.BankTransaction",
        object_id=transaction.id,
        changes={"status": BankTransactionStatus.UNMATCHED},
    )
    return transaction
