"""Money moved between two accounts the organization owns."""

from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from accounting.services.currency import assert_base_currency
from accounting.services.posting import reverse_journal
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from banking.accounting_bridge import post_banking_journal
from banking.models.match import MatchType
from banking.models.statement import BankTransaction, BankTransactionStatus
from banking.models.transfer import BankTransfer, BankTransferStatus
from core.exceptions import ApplicationError

TRANSFER_NUMBER_SEQUENCE_KEY = "bank_transfer"


@db_transaction.atomic
def record_transfer(
    *,
    organization,
    from_bank_account,
    to_bank_account,
    amount: Decimal,
    transfer_date,
    currency=None,
    reference: str = "",
    description: str = "",
    actor=None,
) -> BankTransfer:
    """Create and post a transfer in one act.

    Journal: Dr the destination's ledger account, Cr the source's. Net zero
    for the business — nothing here may touch income or expense, which is the
    whole point. A transfer mis-booked as income is the most common way a
    self-serve ledger overstates revenue, so there is deliberately no
    parameter here that could route either leg anywhere but a bank account's
    own ledger account.
    """
    for label, bank_account in (("from", from_bank_account), ("to", to_bank_account)):
        if bank_account.organization_id != organization.id:
            raise ApplicationError(
                f"The {label} bank account belongs to another organization.", code="cross_org_reference"
            )
        if not bank_account.is_active:
            raise ApplicationError(
                f"The {label} bank account is inactive.", code="bank_account_inactive"
            )
    if from_bank_account.id == to_bank_account.id:
        raise ApplicationError(
            "A transfer needs two different accounts.", code="transfer_same_account"
        )
    if amount is None or amount <= 0:
        raise ApplicationError("Transfer amount must be positive.", code="transfer_amount_invalid")
    if from_bank_account.currency_id != to_bank_account.currency_id:
        # A cross-currency transfer moves one amount out and a different
        # amount in, and the gap is an FX gain or loss that needs an account
        # this phase has no convention for. Inventing one would post a
        # plausible-looking number to an account nobody configured.
        raise ApplicationError(
            "Transfers between accounts in different currencies are not supported yet.",
            code="transfer_currency_mismatch",
        )

    currency = currency or from_bank_account.currency
    assert_base_currency(organization=organization, currency=currency)
    transfer_number = allocate_sequence_number(
        organization_id=organization.id, key=TRANSFER_NUMBER_SEQUENCE_KEY, prefix="TRF-"
    )
    journal = post_banking_journal(
        organization=organization,
        posting_date=transfer_date,
        currency=currency,
        lines=[
            {"account_id": to_bank_account.account_id, "debit": amount},
            {"account_id": from_bank_account.account_id, "credit": amount},
        ],
        memo=f"Transfer {transfer_number}",
        source_type="banking.BankTransfer",
        source_id=transfer_number,
        actor=actor,
    )
    transfer = BankTransfer.objects.create(
        organization=organization,
        transfer_number=transfer_number,
        from_bank_account=from_bank_account,
        to_bank_account=to_bank_account,
        transfer_date=transfer_date,
        amount=amount,
        currency=currency,
        reference=reference,
        description=description,
        accounting_journal=journal,
        created_by=actor,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="banking.BankTransfer",
        object_id=transfer.id,
        changes={
            "transfer_number": transfer_number,
            "amount": str(amount),
            "from": str(from_bank_account.id),
            "to": str(to_bank_account.id),
            "journal_id": str(journal.id),
        },
    )
    return transfer


@db_transaction.atomic
def void_transfer(*, transfer_id, organization, actor=None, reason: str = "") -> BankTransfer:
    """Reverse a transfer's journal. Idempotent — voiding a VOID transfer is
    a no-op, the same construction as `purchases.void_expense`."""
    transfer = (
        BankTransfer.objects.select_for_update().filter(pk=transfer_id, organization=organization).first()
    )
    if transfer is None:
        raise ApplicationError("Transfer not found.", code="transfer_not_found", status_code=404)
    if transfer.status == BankTransferStatus.VOID:
        return transfer
    if transfer.bank_matches.filter(is_confirmed=True).exists():
        # A statement line is currently explained by this transfer. Voiding
        # underneath it would leave the line pointing at a reversed journal
        # and the account silently unreconciled.
        raise ApplicationError(
            "Unmatch the statement lines explained by this transfer before voiding it.",
            code="transfer_has_matches",
        )

    if transfer.accounting_journal_id:
        reverse_journal(
            journal_id=transfer.accounting_journal_id,
            organization=organization,
            actor=actor,
            posting_date=timezone.now().date(),
            memo=f"Void of transfer {transfer.transfer_number}",
        )
    transfer.status = BankTransferStatus.VOID
    transfer.voided_by = actor
    transfer.voided_at = timezone.now()
    transfer.void_reason = reason[:255]
    transfer.save(update_fields=["status", "voided_by", "voided_at", "void_reason", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="banking.BankTransfer",
        object_id=transfer.id,
        changes={"reason": transfer.void_reason},
    )
    return transfer


@db_transaction.atomic
def confirm_detected_transfer(
    *, organization, outflow_transaction_id, inflow_transaction_id, actor=None, reference: str = ""
) -> BankTransfer:
    """Turn a detected pair of statement lines into a recorded transfer.

    This is the one place transfer detection posts anything, and it posts
    exactly one journal for the pair — not one per line. Two lines are two
    views of a single movement; booking each separately would double it and
    leave both accounts wrong in opposite directions, which nets to zero on
    the trial balance and is therefore invisible until someone reads the cash
    flow statement.
    """
    from banking.services.matching import _assert_open_for_changes, create_match

    outflow = (
        BankTransaction.objects.select_for_update()
        .filter(pk=outflow_transaction_id, organization=organization)
        .first()
    )
    inflow = (
        BankTransaction.objects.select_for_update()
        .filter(pk=inflow_transaction_id, organization=organization)
        .first()
    )
    if outflow is None or inflow is None:
        raise ApplicationError("Bank transaction not found.", code="bank_transaction_not_found", status_code=404)
    if outflow.id == inflow.id:
        raise ApplicationError("A transfer needs two different statement lines.", code="transfer_same_line")
    if outflow.bank_account_id == inflow.bank_account_id:
        raise ApplicationError(
            "Both statement lines are on the same account.", code="transfer_same_account"
        )
    if outflow.amount >= 0 or inflow.amount <= 0:
        raise ApplicationError(
            "A transfer pairs one outgoing line with one incoming line.",
            code="transfer_direction_invalid",
        )
    if outflow.amount != -inflow.amount:
        raise ApplicationError(
            "The two statement lines are not the same amount.", code="transfer_amount_mismatch"
        )
    for line in (outflow, inflow):
        _assert_open_for_changes(line)
        if line.status in (BankTransactionStatus.MATCHED, BankTransactionStatus.EXCLUDED):
            raise ApplicationError(
                "Both statement lines must still be open.", code="transfer_line_not_open"
            )

    transfer = record_transfer(
        organization=organization,
        from_bank_account=outflow.bank_account,
        to_bank_account=inflow.bank_account,
        # The outflow leg's date is the one the money left on; a same-day or
        # next-day arrival does not change when the transfer happened.
        transfer_date=outflow.transaction_date,
        amount=abs(outflow.amount),
        reference=reference,
        description="Detected from matching statement lines",
        actor=actor,
    )
    for line in (outflow, inflow):
        create_match(
            transaction_id=line.id,
            organization=organization,
            counterpart_field="bank_transfer",
            counterpart=transfer,
            match_type=MatchType.TRANSFER,
            reason=f"Transfer {transfer.transfer_number}",
            actor=actor,
            confirm=True,
        )
    return transfer
