"""Statement file parsing: untrusted text in, `ParsedTransaction` records out.

WHY ONLY CSV.
The build brief allows OFX/QIF "where justified". CSV is implemented in full;
OFX and QIF are not, and the reason is worth stating rather than leaving as an
absence. A correct OFX reader is an SGML-ish parser with a version split
(SGML-era 1.x vs XML 2.x), institution-specific quirks and its own
entity-expansion attack surface — the kind of thing that needs a maintained
library and real sample files from real banks to test against. Hand-rolling
one from memory would produce a parser that appears to work on the one sample
its author imagined, which on financial data is worse than not offering the
format at all. `StatementFormat` already carries OFX/QIF, and adding one is a
new function behind `parse_statement` plus a justified dependency — no change
to dedupe, rules or matching.

WHY THE COLUMN MAPPING IS REQUIRED AND NOT GUESSED.
Bank CSV headers are not standardized. Inferring which column is the amount,
or whether a withdrawal column is already negative, is a guess — and a wrong
guess books every withdrawal as a deposit, doubling the apparent cash
position. `suggest_column_mapping` exists to help a UI pre-fill the form, but
nothing in the import path calls it: a human confirms the mapping, always.
"""

import csv
import datetime
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from banking.providers.base import ParsedTransaction
from core.exceptions import ApplicationError

# Bounds on untrusted input. A statement with more rows than this is either
# not a statement or needs a chunked import; either way, guessing is worse
# than refusing with a clear message.
MAX_ROWS = 20_000
MAX_FILE_BYTES = 10 * 1024 * 1024

DEFAULT_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d/%m/%y",
    "%d-%m-%y",
)

# Characters banks decorate amounts with. Stripped before Decimal parsing;
# anything still unparseable afterwards is an error, never a silent zero.
_AMOUNT_NOISE = str.maketrans(
    # The NO-BREAK SPACE and currency symbols here are the literal characters
    # banks emit in amount cells, not typos - stripping them is the point, so
    # ruff's ambiguous-character warning is suppressed deliberately.
    {",": "", " ": "", " ": "", "₹": "", "$": "", "£": "", "€": ""}  # noqa: RUF001
)


class AmountMode:
    """How the file expresses direction.

    SIGNED            one column, already negative for money out.
    DEBIT_CREDIT      two columns, at most one populated per row.
    INDICATOR         one unsigned column plus a Dr/Cr marker column.
    """

    SIGNED = "signed"
    DEBIT_CREDIT = "debit_credit"
    INDICATOR = "indicator"


@dataclass
class CsvColumnMapping:
    """Which header carries what. Supplied by the caller, never inferred."""

    date_column: str
    amount_mode: str = AmountMode.SIGNED
    amount_column: str = ""
    debit_column: str = ""
    credit_column: str = ""
    indicator_column: str = ""
    debit_indicators: tuple = ("dr", "debit", "d", "w", "withdrawal")
    credit_indicators: tuple = ("cr", "credit", "c", "d/", "deposit")
    description_column: str = ""
    counterparty_column: str = ""
    reference_column: str = ""
    external_id_column: str = ""
    date_formats: tuple = DEFAULT_DATE_FORMATS
    # Some banks export withdrawals as positive numbers in a column named
    # "Debit". Flipping is a per-file fact the user confirms, not something
    # to detect from the data.
    invert_sign: bool = False
    extra: dict = field(default_factory=dict)


def _normalize_header(value: str) -> str:
    return (value or "").strip().lower().replace("_", " ").replace(".", "").strip()


def _parse_date(raw: str, formats) -> datetime.date:
    text = (raw or "").strip()
    if not text:
        raise ApplicationError("A statement row has no date.", code="statement_row_date_missing")
    for fmt in formats:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ApplicationError(
        f"Could not read '{text}' as a date. Expected one of: {', '.join(formats)}.",
        code="statement_row_date_unparseable",
    )


def _parse_amount(raw: str) -> Decimal:
    """Decimal only — never float, per root CLAUDE.md. Returns 0 for a blank
    cell, which is meaningful in DEBIT_CREDIT mode (the unused column) and is
    rejected later in SIGNED mode by the amount-nonzero check."""
    text = (raw or "").strip()
    if not text:
        return Decimal("0")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.translate(_AMOUNT_NOISE)
    # Trailing sign markers, e.g. "1200.00-" or "1200.00 CR".
    for suffix, sign in (("-", -1), ("cr", 1), ("dr", -1)):
        if text.lower().endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            if sign < 0:
                negative = True
            break
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ApplicationError(
            f"Could not read '{raw}' as an amount.", code="statement_row_amount_unparseable"
        )
    return -value if negative else value


def _row_amount(row: dict, mapping: CsvColumnMapping) -> Decimal:
    if mapping.amount_mode == AmountMode.SIGNED:
        amount = _parse_amount(row.get(_normalize_header(mapping.amount_column), ""))
    elif mapping.amount_mode == AmountMode.DEBIT_CREDIT:
        debit = abs(_parse_amount(row.get(_normalize_header(mapping.debit_column), "")))
        credit = abs(_parse_amount(row.get(_normalize_header(mapping.credit_column), "")))
        if debit and credit:
            # Both populated means the mapping is wrong or the file is
            # malformed. Picking one would silently halve or double the row.
            raise ApplicationError(
                "A statement row has both a debit and a credit amount.",
                code="statement_row_ambiguous_amount",
            )
        amount = credit - debit
    elif mapping.amount_mode == AmountMode.INDICATOR:
        magnitude = abs(_parse_amount(row.get(_normalize_header(mapping.amount_column), "")))
        marker = (row.get(_normalize_header(mapping.indicator_column), "") or "").strip().lower()
        if marker in {m.lower() for m in mapping.credit_indicators}:
            amount = magnitude
        elif marker in {m.lower() for m in mapping.debit_indicators}:
            amount = -magnitude
        else:
            raise ApplicationError(
                f"Could not read '{marker}' as a debit/credit indicator.",
                code="statement_row_indicator_unknown",
            )
    else:
        raise ApplicationError(f"Unknown amount mode '{mapping.amount_mode}'.", code="statement_amount_mode_invalid")

    if mapping.invert_sign:
        amount = -amount
    return amount


def _validate_mapping(mapping: CsvColumnMapping) -> None:
    if not mapping.date_column:
        raise ApplicationError("date_column is required.", code="statement_mapping_invalid")
    if mapping.amount_mode == AmountMode.SIGNED and not mapping.amount_column:
        raise ApplicationError("amount_column is required in signed mode.", code="statement_mapping_invalid")
    if mapping.amount_mode == AmountMode.DEBIT_CREDIT and not (mapping.debit_column and mapping.credit_column):
        raise ApplicationError(
            "debit_column and credit_column are both required in debit/credit mode.",
            code="statement_mapping_invalid",
        )
    if mapping.amount_mode == AmountMode.INDICATOR and not (mapping.amount_column and mapping.indicator_column):
        raise ApplicationError(
            "amount_column and indicator_column are both required in indicator mode.",
            code="statement_mapping_invalid",
        )


def parse_csv_statement(*, content: str, mapping: CsvColumnMapping) -> list[ParsedTransaction]:
    """Parse a CSV bank statement into normalized records.

    Rows whose amount comes out as exactly zero are DROPPED, not imported:
    every real bank CSV carries some, either as a balance-brought-forward
    header line or as a narration continuation, and a zero-amount statement
    line would match any zero-amount counterpart while explaining nothing.
    """
    _validate_mapping(mapping)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ApplicationError("Statement file is too large.", code="statement_file_too_large")

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ApplicationError("Statement file has no header row.", code="statement_no_header")

    normalized_headers = [_normalize_header(name) for name in reader.fieldnames]
    for label, column in (
        ("date_column", mapping.date_column),
        ("amount_column", mapping.amount_column),
        ("debit_column", mapping.debit_column),
        ("credit_column", mapping.credit_column),
        ("indicator_column", mapping.indicator_column),
        ("description_column", mapping.description_column),
        ("counterparty_column", mapping.counterparty_column),
        ("reference_column", mapping.reference_column),
        ("external_id_column", mapping.external_id_column),
    ):
        if column and _normalize_header(column) not in normalized_headers:
            raise ApplicationError(
                f"{label} '{column}' is not a column in this file.", code="statement_column_missing"
            )

    def cell(row: dict, column: str) -> str:
        if not column:
            return ""
        return (row.get(_normalize_header(column)) or "").strip()

    parsed: list[ParsedTransaction] = []
    for index, raw_row in enumerate(reader, start=2):  # start=2: row 1 is the header
        if index - 1 > MAX_ROWS:
            raise ApplicationError(
                f"Statement file has more than {MAX_ROWS} rows.", code="statement_too_many_rows"
            )
        row = {_normalize_header(k): (v or "") for k, v in raw_row.items() if k is not None}
        if not any(value.strip() for value in row.values()):
            continue
        try:
            transaction_date = _parse_date(cell(row, mapping.date_column), mapping.date_formats)
            amount = _row_amount(row, mapping)
        except ApplicationError as exc:
            # Re-raised with the row number: "could not read an amount" is
            # useless on a 900-row file.
            raise ApplicationError(f"Row {index}: {exc.detail}", code=exc.get_codes())
        if amount == 0:
            continue
        parsed.append(
            ParsedTransaction(
                transaction_date=transaction_date,
                amount=amount,
                description=cell(row, mapping.description_column),
                counterparty_name=cell(row, mapping.counterparty_column),
                bank_reference=cell(row, mapping.reference_column),
                external_id=cell(row, mapping.external_id_column),
                raw=dict(row),
            )
        )
    return parsed


# Header words seen on real bank exports, used ONLY to pre-fill a mapping form
# for a human to confirm. Nothing in the import path consults this.
_SUGGESTIONS = {
    "date_column": ("date", "transaction date", "txn date", "value date", "posting date"),
    "amount_column": ("amount", "transaction amount", "amt"),
    "debit_column": ("debit", "withdrawal", "withdrawal amt", "debit amount", "paid out", "money out"),
    "credit_column": ("credit", "deposit", "deposit amt", "credit amount", "paid in", "money in"),
    "description_column": ("description", "narration", "particulars", "details", "transaction remarks"),
    "reference_column": ("reference", "ref", "cheque no", "chq no", "transaction id", "utr"),
}


def suggest_column_mapping(*, header_row: list) -> dict:
    """Best-guess column mapping for a UI to PRE-FILL. Advisory only.

    Kept deliberately separate from `parse_csv_statement`, which accepts no
    guesses: this returns a suggestion for a person to confirm, in exactly the
    same spirit as the matcher suggesting rather than posting.
    """
    normalized = {_normalize_header(name): name for name in header_row}
    suggestion = {}
    for field_name, candidates in _SUGGESTIONS.items():
        for candidate in candidates:
            if candidate in normalized:
                suggestion[field_name] = normalized[candidate]
                break
    if "debit_column" in suggestion and "credit_column" in suggestion:
        suggestion["amount_mode"] = AmountMode.DEBIT_CREDIT
        suggestion.pop("amount_column", None)
    elif "amount_column" in suggestion:
        suggestion["amount_mode"] = AmountMode.SIGNED
        suggestion.pop("debit_column", None)
        suggestion.pop("credit_column", None)
    return suggestion
