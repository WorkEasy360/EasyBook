"""Statement ingestion: parsed rows become BankTransactions, exactly once.

THE DUPLICATE PROBLEM, AND WHY THE OBVIOUS FIX IS WRONG.

The realistic way a user duplicates a statement is not re-uploading the same
file — that is caught by the file hash and is the easy case. It is
downloading 1 to 31 March on the 31st, then 15 March to 15 April a
fortnight later. The bytes differ; roughly half the rows are the same.

The tempting fix is a unique constraint on (account, date, amount,
description). It is wrong, and wrong in the direction that loses money: two
500.00 ATM withdrawals on the same day at the same machine produce byte-
identical rows and are both real. A uniqueness rule silently discards the
second, and the account never reconciles again.

So duplicates are resolved as a MULTISET difference. For each distinct
fingerprint the file is asked "how many of these do you contain?" and the
database "how many do you already hold?", and only the excess is imported. Two
genuine withdrawals in one file import as two; the same two seen again in an
overlapping file import as none; a bank that splits them across two exports
imports one then one.

That leaves one case the arithmetic cannot decide: the bank really did charge
an identical amount on an identical day in a LATER statement, with no id to
tell them apart. No general rule can distinguish that from an overlap, so it
is not guessed — `services/transactions.py::add_manual_transaction` lets a
person add it deliberately, which is the only honest resolution.
"""

import hashlib
import re

from django.db import IntegrityError, transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from banking.models.bank_account import BankAccount
from banking.models.statement import (
    BankTransaction,
    StatementFormat,
    StatementImport,
    StatementImportStatus,
)
from banking.services.parsers import parse_csv_statement
from core.exceptions import ApplicationError

_WHITESPACE = re.compile(r"\s+")


def normalize_narration(value: str) -> str:
    """Bank narrations vary by whitespace and case between exports of the same
    transaction, so both are normalized away before fingerprinting. Nothing
    else is: stripping punctuation or digits would fold genuinely different
    references onto one another."""
    return _WHITESPACE.sub(" ", (value or "").strip()).upper()


def compute_fingerprint(*, transaction_date, amount, description="", bank_reference="", external_id="") -> str:
    """Stable identity for a statement line.

    When the bank supplies its own id that id IS the fingerprint, because an
    institution-issued identifier is a stronger duplicate guard than anything
    derivable from the visible columns — and because a bank that reissues a
    statement with a tidied-up narration would otherwise produce a different
    fingerprint for the same transaction.
    """
    if external_id:
        payload = f"id:{external_id}"
    else:
        payload = "|".join(
            [
                str(transaction_date),
                str(amount),
                normalize_narration(description),
                normalize_narration(bank_reference),
            ]
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _lock_bank_account(*, bank_account_id, organization) -> BankAccount:
    """Serializes imports for one account.

    Without this, two concurrent uploads of overlapping ranges each count the
    rows already present, each see the same count, and each import the same
    excess — the identical read-then-write race that let one goods receipt be
    billed five times (purchases/services/bills.py). The unique constraint on
    (bank_account, fingerprint, duplicate_ordinal) is the backstop; this lock
    is what makes the outcome correct rather than merely non-corrupt.
    """
    locked = (
        BankAccount.objects.select_for_update().filter(pk=bank_account_id, organization=organization).first()
    )
    if locked is None:
        raise ApplicationError("Bank account not found.", code="bank_account_not_found", status_code=404)
    return locked


def _existing_fingerprint_counts(*, bank_account, fingerprints: set) -> dict:
    counts = {}
    rows = (
        BankTransaction.all_objects.filter(bank_account=bank_account, fingerprint__in=fingerprints)
        .values_list("fingerprint", flat=True)
    )
    for fingerprint in rows:
        counts[fingerprint] = counts.get(fingerprint, 0) + 1
    return counts


@transaction.atomic
def import_parsed_transactions(
    *,
    organization,
    bank_account,
    parsed_rows: list,
    source_format: str = StatementFormat.CSV,
    file_name: str = "",
    file_hash: str = "",
    actor=None,
) -> StatementImport:
    """Persist normalized rows, skipping the ones already held.

    Posts NO accounting. A statement line is the bank's account of what
    happened, not ours; the journal (if any) appears when the line is matched
    or categorized. See banking/CLAUDE.md.
    """
    bank_account = _lock_bank_account(bank_account_id=bank_account.pk, organization=organization)
    if not bank_account.is_active:
        raise ApplicationError(
            "Cannot import a statement into an inactive bank account.", code="bank_account_inactive"
        )

    statement_import = StatementImport(
        organization=organization,
        bank_account=bank_account,
        source_format=source_format,
        file_name=file_name,
        file_hash=file_hash,
        rows_read=len(parsed_rows),
        created_by=actor,
    )
    try:
        with transaction.atomic():
            statement_import.save()
    except IntegrityError:
        # The file-hash unique constraint. Distinct from the per-row guard
        # below and much blunter, but it lets the common "clicked upload
        # twice" case fail with a sentence a user can act on.
        raise ApplicationError(
            "This exact statement file has already been imported into this account.",
            code="statement_already_imported",
        )

    prepared = []
    for row in parsed_rows:
        if row.amount == 0:
            continue
        prepared.append(
            {
                "row": row,
                "fingerprint": compute_fingerprint(
                    transaction_date=row.transaction_date,
                    amount=row.amount,
                    description=row.description,
                    bank_reference=row.bank_reference,
                    external_id=row.external_id,
                ),
            }
        )

    existing_counts = _existing_fingerprint_counts(
        bank_account=bank_account, fingerprints={p["fingerprint"] for p in prepared}
    )

    seen_in_batch = {}
    to_create = []
    skipped = 0
    dates = []
    for item in prepared:
        fingerprint = item["fingerprint"]
        occurrence = seen_in_batch.get(fingerprint, 0)
        seen_in_batch[fingerprint] = occurrence + 1
        if occurrence < existing_counts.get(fingerprint, 0):
            # The database already holds this many of this line. Only the
            # excess is new.
            skipped += 1
            continue
        row = item["row"]
        dates.append(row.transaction_date)
        to_create.append(
            BankTransaction(
                organization=organization,
                bank_account=bank_account,
                statement_import=statement_import,
                transaction_date=row.transaction_date,
                amount=row.amount,
                description=row.description,
                counterparty_name=row.counterparty_name,
                bank_reference=row.bank_reference,
                external_id=row.external_id,
                fingerprint=fingerprint,
                duplicate_ordinal=occurrence,
            )
        )

    if to_create:
        BankTransaction.objects.bulk_create(to_create)

    statement_import.rows_imported = len(to_create)
    statement_import.rows_skipped_duplicate = skipped
    statement_import.statement_start_date = min(dates) if dates else None
    statement_import.statement_end_date = max(dates) if dates else None
    statement_import.status = StatementImportStatus.COMPLETED
    statement_import.save(
        update_fields=[
            "rows_imported",
            "rows_skipped_duplicate",
            "statement_start_date",
            "statement_end_date",
            "status",
            "updated_at",
        ]
    )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="banking.StatementImport",
        object_id=statement_import.id,
        changes={
            "bank_account": str(bank_account.id),
            "rows_read": statement_import.rows_read,
            "rows_imported": statement_import.rows_imported,
            "rows_skipped_duplicate": skipped,
        },
    )
    return statement_import


@transaction.atomic
def import_csv_statement(
    *, organization, bank_account, content: str, mapping, file_name: str = "", actor=None
) -> StatementImport:
    """Parse and import a CSV statement in one call."""
    parsed_rows = parse_csv_statement(content=content, mapping=mapping)
    return import_parsed_transactions(
        organization=organization,
        bank_account=bank_account,
        parsed_rows=parsed_rows,
        source_format=StatementFormat.CSV,
        file_name=file_name,
        file_hash=compute_file_hash(content),
        actor=actor,
    )
