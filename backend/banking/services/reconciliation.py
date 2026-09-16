"""Period reconciliation: the formal sign-off.

COMPLETION IS THE CONTROL, SO IT HAS NO OVERRIDE.

`complete_reconciliation` refuses unless two things hold: the cleared balance
equals the closing balance the user read off the statement, to the cent; and
no line in the period is still unmatched or merely suggested. There is no
"force close" flag, and its absence is the feature. A reconciliation that can
be closed over an unexplained difference certifies nothing, and the number it
produces is worse than no number because it carries an assurance it has not
earned.

The escape hatches are the honest ones. A line with no document behind it is
categorized, which posts the journal the books were missing. A line that is
not a real transaction is excluded, with a mandatory reason. Both leave a
record with an author; forcing a close would leave a difference with nobody's
name on it.
"""

from django.db import transaction as db_transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from banking.models.reconciliation import BankReconciliation, ReconciliationStatus
from banking.models.statement import BankTransaction
from banking.selectors import get_cleared_balance, get_reconciliation_summary
from core.exceptions import ApplicationError


def _get_for_update(*, reconciliation_id, organization) -> BankReconciliation:
    locked = (
        BankReconciliation.objects.select_for_update()
        .filter(pk=reconciliation_id, organization=organization)
        .select_related("bank_account")
        .first()
    )
    if locked is None:
        raise ApplicationError("Reconciliation not found.", code="reconciliation_not_found", status_code=404)
    return locked


@db_transaction.atomic
def start_reconciliation(
    *,
    organization,
    bank_account,
    statement_start_date,
    statement_end_date,
    statement_closing_balance,
    notes: str = "",
    actor=None,
) -> BankReconciliation:
    if bank_account.organization_id != organization.id:
        raise ApplicationError("Bank account must belong to this organization.", code="cross_org_reference")
    if statement_end_date < statement_start_date:
        raise ApplicationError(
            "The statement period ends before it starts.", code="reconciliation_dates_invalid"
        )
    if BankReconciliation.objects.filter(
        bank_account=bank_account, status=ReconciliationStatus.IN_PROGRESS
    ).exists():
        raise ApplicationError(
            "This account already has a reconciliation in progress.", code="reconciliation_already_open"
        )
    overlapping = BankReconciliation.objects.filter(
        bank_account=bank_account,
        status=ReconciliationStatus.COMPLETED,
        statement_end_date__gte=statement_start_date,
    ).exists()
    if overlapping:
        # Lines inside a completed period are locked, so an overlapping
        # period could never be closed — it would immediately contain
        # transactions it is not allowed to touch.
        raise ApplicationError(
            "This period overlaps a reconciliation that has already been completed.",
            code="reconciliation_period_overlap",
        )

    reconciliation = BankReconciliation.objects.create(
        organization=organization,
        bank_account=bank_account,
        statement_start_date=statement_start_date,
        statement_end_date=statement_end_date,
        statement_closing_balance=statement_closing_balance,
        notes=notes,
        created_by=actor,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="banking.BankReconciliation",
        object_id=reconciliation.id,
        changes={
            "bank_account": str(bank_account.id),
            "period": f"{statement_start_date}..{statement_end_date}",
            "closing_balance": str(statement_closing_balance),
        },
    )
    return reconciliation


@db_transaction.atomic
def complete_reconciliation(*, reconciliation_id, organization, actor=None) -> BankReconciliation:
    """Close the period, freezing its cleared balance and locking its lines."""
    reconciliation = _get_for_update(reconciliation_id=reconciliation_id, organization=organization)
    if reconciliation.status == ReconciliationStatus.COMPLETED:
        # Idempotent by construction, like post_journal/post_bill: repeating
        # a completed close is a locked no-op, not a second close.
        return reconciliation
    if reconciliation.status != ReconciliationStatus.IN_PROGRESS:
        raise ApplicationError(
            f"Cannot complete a reconciliation in status '{reconciliation.status}'.",
            code="reconciliation_invalid_status",
        )

    summary = get_reconciliation_summary(reconciliation=reconciliation)
    if summary["open_transaction_count"]:
        raise ApplicationError(
            f"{summary['open_transaction_count']} transaction(s) in this period are still unmatched.",
            code="reconciliation_has_open_transactions",
        )
    if not summary["is_balanced"]:
        raise ApplicationError(
            f"The statement closing balance differs from the cleared balance by "
            f"{summary['difference']}.",
            code="reconciliation_out_of_balance",
        )

    in_period = BankTransaction.objects.filter(
        bank_account=reconciliation.bank_account,
        transaction_date__gte=reconciliation.statement_start_date,
        transaction_date__lte=reconciliation.statement_end_date,
        reconciliation__isnull=True,
    )
    locked_count = in_period.update(reconciliation=reconciliation)

    reconciliation.cleared_balance = get_cleared_balance(
        bank_account=reconciliation.bank_account, as_of=reconciliation.statement_end_date
    )
    reconciliation.status = ReconciliationStatus.COMPLETED
    reconciliation.completed_by = actor
    reconciliation.completed_at = timezone.now()
    reconciliation.save(
        update_fields=["cleared_balance", "status", "completed_by", "completed_at", "updated_at"]
    )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="banking.BankReconciliation",
        object_id=reconciliation.id,
        changes={
            "cleared_balance": str(reconciliation.cleared_balance),
            "statement_closing_balance": str(reconciliation.statement_closing_balance),
            "transactions_locked": locked_count,
        },
    )
    return reconciliation


@db_transaction.atomic
def reopen_reconciliation(*, reconciliation_id, organization, reason: str, actor=None) -> BankReconciliation:
    """Unlock a completed period so its lines can be corrected.

    Reopening is a recorded act with a mandatory reason, and it is the ONLY
    way to touch a line inside a closed period. That is the difference
    between correcting a reconciliation and quietly editing underneath one:
    both end with the same data, only one leaves evidence that the signed-off
    figure changed.

    The frozen `cleared_balance` is deliberately left in place while reopen
    is in progress — it is the record of what was certified, and it is
    recomputed only when the period is completed again.
    """
    if not (reason or "").strip():
        raise ApplicationError(
            "Reopening a completed reconciliation requires a reason.",
            code="reopen_reason_required",
        )
    reconciliation = _get_for_update(reconciliation_id=reconciliation_id, organization=organization)
    if reconciliation.status != ReconciliationStatus.COMPLETED:
        raise ApplicationError(
            "Only a completed reconciliation can be reopened.", code="reconciliation_not_completed"
        )
    if BankReconciliation.objects.filter(
        bank_account=reconciliation.bank_account, status=ReconciliationStatus.IN_PROGRESS
    ).exists():
        raise ApplicationError(
            "Close the reconciliation currently in progress on this account first.",
            code="reconciliation_already_open",
        )

    reconciliation.status = ReconciliationStatus.IN_PROGRESS
    reconciliation.completed_by = None
    reconciliation.completed_at = None
    reconciliation.notes = f"{reconciliation.notes}\nReopened: {reason.strip()}".strip()
    reconciliation.save(update_fields=["status", "completed_by", "completed_at", "notes", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="banking.BankReconciliation",
        object_id=reconciliation.id,
        changes={"status": ReconciliationStatus.IN_PROGRESS, "reason": reason.strip()},
    )
    return reconciliation


@db_transaction.atomic
def abandon_reconciliation(*, reconciliation_id, organization, reason: str = "", actor=None) -> BankReconciliation:
    """Give up on an in-progress reconciliation without closing it.

    Distinct from completing: nothing is certified and no line is locked, so
    the period can be started again later. Kept rather than deleted so the
    attempt itself stays visible.
    """
    reconciliation = _get_for_update(reconciliation_id=reconciliation_id, organization=organization)
    if reconciliation.status != ReconciliationStatus.IN_PROGRESS:
        raise ApplicationError(
            "Only an in-progress reconciliation can be abandoned.",
            code="reconciliation_invalid_status",
        )
    reconciliation.status = ReconciliationStatus.ABANDONED
    reconciliation.notes = f"{reconciliation.notes}\nAbandoned: {reason}".strip()
    reconciliation.save(update_fields=["status", "notes", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="banking.BankReconciliation",
        object_id=reconciliation.id,
        changes={"status": ReconciliationStatus.ABANDONED, "reason": reason},
    )
    return reconciliation
