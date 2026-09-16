"""GST/Tax reports — pure pass-through to `compliance.selectors` (PHASE 8
spec §14). No tax/compliance calculation is duplicated here; this module
exists only so the reports API surface has its own thin, testable import
boundary, the same pattern as reports.selectors.gl_journal wrapping
accounting.selectors. `compliance` currently has no HTTP API of its own
(only this module and reports/api/views.py expose it over REST) — these
are also the FIRST GST report endpoints this codebase has ever served.
"""

from compliance.selectors import (
    get_gstr1_summary as _get_gstr1_summary,
)
from compliance.selectors import (
    get_gstr3b_summary as _get_gstr3b_summary,
)
from compliance.selectors import (
    get_input_tax_register as _get_input_tax_register,
)
from compliance.selectors import (
    get_output_tax_register as _get_output_tax_register,
)


def get_output_tax_register(*, organization, date_from, date_to):
    return _get_output_tax_register(organization=organization, date_from=date_from, date_to=date_to)


def get_input_tax_register(*, organization, date_from, date_to):
    return _get_input_tax_register(organization=organization, date_from=date_from, date_to=date_to)


def get_gstr1_summary(*, organization, date_from, date_to):
    return _get_gstr1_summary(organization=organization, date_from=date_from, date_to=date_to)


def get_gstr3b_summary(*, organization, date_from, date_to):
    return _get_gstr3b_summary(organization=organization, date_from=date_from, date_to=date_to)


def get_gst_summary(*, organization, date_from, date_to) -> dict:
    """One combined view: output/input tax totals and the two return
    summaries, for a single 'GST Summary' report screen."""
    output_register = get_output_tax_register(organization=organization, date_from=date_from, date_to=date_to)
    input_register = get_input_tax_register(organization=organization, date_from=date_from, date_to=date_to)

    from decimal import Decimal

    zero = Decimal("0")

    def _totals(rows):
        return {
            "taxable_value": sum((row["taxable_value"] for row in rows), zero),
            "cgst": sum((row["cgst"] for row in rows), zero),
            "sgst": sum((row["sgst"] for row in rows), zero),
            "igst": sum((row["igst"] for row in rows), zero),
            "cess": sum((row["cess"] for row in rows), zero),
        }

    return {
        "period": {"from_date": date_from, "to_date": date_to},
        "output_tax": _totals(output_register),
        "input_tax": _totals(input_register),
        "gstr1": get_gstr1_summary(organization=organization, date_from=date_from, date_to=date_to),
        "gstr3b": get_gstr3b_summary(organization=organization, date_from=date_from, date_to=date_to),
    }
