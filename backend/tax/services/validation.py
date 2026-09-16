"""GSTIN and PAN validation.

`purchases/models/vendor.py` has carried the comment "Phase 7 owns GSTIN
validation" since Phase 4; this is that owner. Until now `gstin` was a
`CharField(15)` that accepted any fifteen characters, including the empty
string, on Organization, Customer and Vendor alike.

Validation is structural, not existential: it proves a GSTIN is well-formed,
NOT that it is registered, active, or belongs to the party that supplied it.
Only the GST portal can answer that, and this codebase has no credentials for
it (root CLAUDE.md rule 5). A well-formed GSTIN for a cancelled registration
passes here and should - refusing it would block legitimate historical data
entry.
"""

import re
import string

from core.exceptions import ApplicationError

# 0-9 then A-Z. Position in this string IS the character's numeric value, which
# is what makes the checksum arithmetic below work.
_ALPHABET = string.digits + string.ascii_uppercase
_ALPHABET_SIZE = len(_ALPHABET)  # 36

GSTIN_LENGTH = 15

# 5 letters, 4 digits, 1 letter - the PAN block occupying GSTIN positions 3-12.
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


def compute_gstin_check_character(first_fourteen: str) -> str:
    """The 15th character, by the MOD-36 scheme GSTN uses.

    Each of the first fourteen characters is valued by its index in `_ALPHABET`
    and multiplied by an alternating 1, 2 factor (1 for position 0). Each
    product contributes `quotient + remainder` of a division by 36, the total
    is subtracted from the next multiple of 36, and the result indexes back
    into the alphabet.

    Exposed rather than kept private so tests can assert the checksum both ways
    - that a known-good GSTIN verifies, and that changing any single character
    of it stops verifying.
    """
    total = 0
    for index, char in enumerate(first_fourteen):
        value = _ALPHABET.index(char)
        factor = 2 if index % 2 else 1
        product = value * factor
        total += (product // _ALPHABET_SIZE) + (product % _ALPHABET_SIZE)
    check_value = (_ALPHABET_SIZE - (total % _ALPHABET_SIZE)) % _ALPHABET_SIZE
    return _ALPHABET[check_value]


def validate_pan(value: str) -> str:
    """Normalizes and structurally validates a PAN. Blank is allowed - PAN is
    optional master data, and a blank field is "not recorded", not "invalid"."""
    if not value:
        return ""
    normalized = value.strip().upper()
    if not _PAN_RE.match(normalized):
        raise ApplicationError(
            "PAN must be 5 letters, 4 digits, then 1 letter.", code="pan_invalid_format"
        )
    return normalized


def validate_gstin(value: str, *, known_state_codes=None) -> str:
    """Normalizes and validates a GSTIN, returning the uppercased value.

    Blank is allowed and returns blank: unregistered customers and vendors are
    ordinary and must remain enterable.

    `known_state_codes` is an optional iterable of valid `StateCode` primary
    keys. It is a parameter rather than a query inside this function so that
    validation stays a pure function - importable from a data migration, usable
    in a loop over ten thousand rows without ten thousand queries, and testable
    without the database. Callers that want the check pass
    `tax.selectors.known_state_codes()`.
    """
    if not value:
        return ""

    normalized = value.strip().upper()

    if len(normalized) != GSTIN_LENGTH:
        raise ApplicationError(
            f"GSTIN must be exactly {GSTIN_LENGTH} characters.", code="gstin_invalid_format"
        )
    if not _GSTIN_RE.match(normalized):
        raise ApplicationError(
            "GSTIN must be 2 digits (state), a 10-character PAN, an entity code, "
            "the letter Z, and a check character.",
            code="gstin_invalid_format",
        )

    if known_state_codes is not None and normalized[:2] not in set(known_state_codes):
        raise ApplicationError(
            f"GSTIN state code '{normalized[:2]}' is not a known GST state code.",
            code="gstin_unknown_state",
        )

    if compute_gstin_check_character(normalized[:14]) != normalized[14]:
        raise ApplicationError(
            "GSTIN check character does not match the rest of the number.",
            code="gstin_checksum_invalid",
        )

    return normalized


def gstin_state_code(value: str) -> str:
    """The first two characters, or "" for a blank GSTIN. Does not validate -
    call `validate_gstin` for that."""
    return value.strip().upper()[:2] if value else ""
