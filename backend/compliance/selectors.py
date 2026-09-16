"""Tax registers and return summaries, derived on demand.

NOTHING HERE IS STORED. These read posted documents and compute, exactly as
`accounting/selectors.py` derives the General Ledger and Trial Balance from
posted `JournalLine` rows and for the same stated reason: a cached return
summary is a number that can silently disagree with the documents behind it,
and the only way it can never go stale is for it not to exist.

WHAT THIS IS NOT
----------------
It does not file anything. It produces the figures a GSTR-1 or GSTR-3B is made
of; submitting them to GSTN needs credentials this codebase does not have
(root CLAUDE.md rule 5, and the same reasoning as compliance/providers/base.py).

It is also not AI-assisted and must not become so: these are authoritative tax
figures, which the root CLAUDE.md pipeline rule reserves for deterministic code.
"""

from collections import defaultdict
from decimal import Decimal

from tax.enums import SupplyNature, TaxTreatment

ZERO = Decimal("0")

# What a return sees: documents that have left DRAFT and have not been voided.
# Built by EXCLUDING the two dead states rather than listing the live ones, so
# a status added later (an "overdue" sweep, say) is included automatically
# instead of silently dropping out of every return.
def _live_statuses(enum, *dead):
    return [value for value in enum.values if value not in dead]


def _zero_bucket():
    return {
        "taxable_value": ZERO,
        "cgst": ZERO,
        "sgst": ZERO,
        "igst": ZERO,
        "cess": ZERO,
        "document_count": 0,
    }


def _add(bucket, *, taxable, cgst, sgst, igst, cess, count=1):
    bucket["taxable_value"] += taxable
    bucket["cgst"] += cgst
    bucket["sgst"] += sgst
    bucket["igst"] += igst
    bucket["cess"] += cess
    bucket["document_count"] += count
    return bucket


def _live_invoices(organization, date_from, date_to):
    from sales.models.invoice import Invoice, InvoiceStatus

    return (
        Invoice.objects.filter(
            organization=organization,
            invoice_date__gte=date_from,
            invoice_date__lte=date_to,
            status__in=_live_statuses(
                InvoiceStatus, InvoiceStatus.DRAFT, InvoiceStatus.VOID
            ),
        )
        .select_related("customer", "place_of_supply")
        .prefetch_related("lines")
    )


def _live_credit_notes(organization, date_from, date_to):
    from sales.models.credit_note import CreditNote, CreditNoteStatus

    return (
        CreditNote.objects.filter(
            organization=organization,
            credit_note_date__gte=date_from,
            credit_note_date__lte=date_to,
            status__in=_live_statuses(
                CreditNoteStatus, CreditNoteStatus.DRAFT, CreditNoteStatus.VOID
            ),
        )
        .select_related("customer", "place_of_supply", "source_invoice")
        .prefetch_related("lines")
    )


def _live_bills(organization, date_from, date_to):
    from purchases.models.bill import Bill, BillStatus

    return (
        Bill.objects.filter(
            organization=organization,
            bill_date__gte=date_from,
            bill_date__lte=date_to,
            status__in=_live_statuses(BillStatus, BillStatus.DRAFT, BillStatus.VOID),
        )
        .select_related("vendor", "place_of_supply")
        .prefetch_related("lines")
    )


def get_output_tax_register(*, organization, date_from, date_to) -> list[dict]:
    """Every outward supply in the period, document by document.

    Credit notes appear with NEGATIVE amounts rather than in a separate list:
    a register's purpose is to total to the period's liability, and a reader
    who has to remember to subtract a second list is a reader who will
    eventually forget.
    """
    rows = []
    for invoice in _live_invoices(organization, date_from, date_to):
        rows.append(
            {
                "document_type": "invoice",
                "document_id": invoice.id,
                "document_number": invoice.invoice_number,
                "document_date": invoice.invoice_date,
                "party": invoice.customer.display_name,
                "gstin": invoice.customer.gstin,
                "place_of_supply": invoice.place_of_supply_id,
                "supply_nature": invoice.supply_nature,
                "is_reverse_charge": invoice.is_reverse_charge,
                "taxable_value": invoice.subtotal - invoice.discount_total,
                "cgst": invoice.cgst_total,
                "sgst": invoice.sgst_total,
                "igst": invoice.igst_total,
                "cess": invoice.cess_total,
                "total": invoice.total,
            }
        )
    for note in _live_credit_notes(organization, date_from, date_to):
        rows.append(
            {
                "document_type": "credit_note",
                "document_id": note.id,
                "document_number": note.credit_note_number,
                "document_date": note.credit_note_date,
                "party": note.customer.display_name,
                "gstin": note.customer.gstin,
                "place_of_supply": note.place_of_supply_id,
                "supply_nature": note.supply_nature,
                "is_reverse_charge": False,
                "taxable_value": -(note.subtotal - note.discount_total),
                "cgst": -note.cgst_total,
                "sgst": -note.sgst_total,
                "igst": -note.igst_total,
                "cess": -note.cess_total,
                "total": -note.total,
            }
        )
    return sorted(rows, key=lambda row: (row["document_date"], row["document_number"]))


def get_input_tax_register(*, organization, date_from, date_to) -> list[dict]:
    """Every inward supply in the period. `is_reverse_charge` is carried
    through because those bills produce a liability as well as a credit, and a
    register that hid the distinction would overstate the net credit."""
    rows = []
    for bill in _live_bills(organization, date_from, date_to):
        rows.append(
            {
                "document_type": "bill",
                "document_id": bill.id,
                "document_number": bill.bill_number,
                "vendor_bill_number": bill.vendor_bill_number,
                "document_date": bill.bill_date,
                "party": bill.vendor.display_name,
                "gstin": bill.vendor.gstin,
                "place_of_supply": bill.place_of_supply_id,
                "supply_nature": bill.supply_nature,
                "is_reverse_charge": bill.is_reverse_charge,
                "taxable_value": bill.subtotal - bill.discount_total,
                "cgst": bill.cgst_total,
                "sgst": bill.sgst_total,
                "igst": bill.igst_total,
                "cess": bill.cess_total,
                "total": bill.total,
            }
        )
    return sorted(rows, key=lambda row: (row["document_date"], row["document_number"]))


def _is_b2b(party) -> bool:
    return party.tax_treatment in {
        TaxTreatment.REGISTERED,
        TaxTreatment.SEZ,
        TaxTreatment.DEEMED_EXPORT,
    } and bool(party.gstin)


def get_gstr1_summary(*, organization, date_from, date_to) -> dict:
    """The figures a GSTR-1 is made of, by table.

    Table numbering follows the form: 4A B2B, 5 B2C large (inter-State above
    the invoice-value threshold), 6A exports, 7 B2C small, 8 nil/exempt,
    9B credit and debit notes, 12 HSN summary.

    Table 12 is returned BIFURCATED into b2b and b2c sub-summaries, which is
    how the form has required it since the 2025 Phase-III change to Table 12
    (GSTN advisory, 22 January 2025).

    The B2C-large threshold is a PARAMETER of the caller's return, not a
    constant here: the figure has moved (most recently to 1,00,000) and
    hard-coding it would make this file wrong on a date nobody controls.
    """
    b2b = defaultdict(_zero_bucket)
    b2c_large = _zero_bucket()
    b2c_small = defaultdict(_zero_bucket)
    exports = defaultdict(_zero_bucket)
    nil_exempt = _zero_bucket()
    credit_notes = defaultdict(_zero_bucket)
    hsn_b2b = defaultdict(_zero_bucket)
    hsn_b2c = defaultdict(_zero_bucket)

    for invoice in _live_invoices(organization, date_from, date_to):
        taxable = invoice.subtotal - invoice.discount_total
        amounts = {
            "taxable": taxable,
            "cgst": invoice.cgst_total,
            "sgst": invoice.sgst_total,
            "igst": invoice.igst_total,
            "cess": invoice.cess_total,
        }
        is_b2b = _is_b2b(invoice.customer)

        if invoice.supply_nature in {
            SupplyNature.EXPORT_WITH_TAX,
            SupplyNature.EXPORT_WITHOUT_TAX,
        }:
            key = (
                "with_payment"
                if invoice.supply_nature == SupplyNature.EXPORT_WITH_TAX
                else "without_payment"
            )
            _add(exports[key], **amounts)
        elif invoice.tax_total == ZERO and invoice.supply_nature != SupplyNature.UNSPECIFIED:
            _add(nil_exempt, **amounts)
        elif is_b2b:
            _add(b2b[invoice.customer.gstin], **amounts)
        elif invoice.supply_nature == SupplyNature.INTER_STATE:
            _add(b2c_large, **amounts)
        else:
            _add(b2c_small[invoice.place_of_supply_id or ""], **amounts)

        # Table 12, HSN-wise. Keyed on (HSN, rate) because the form reports a
        # line per rate within an HSN, not per HSN.
        target = hsn_b2b if is_b2b else hsn_b2c
        for line in invoice.lines.all():
            _add(
                target[(line.hsn_sac_snapshot, line.tax_rate)],
                taxable=line.taxable_amount,
                cgst=line.cgst_amount,
                sgst=line.sgst_amount,
                igst=line.igst_amount,
                cess=line.cess_amount,
            )

    for note in _live_credit_notes(organization, date_from, date_to):
        key = "registered" if _is_b2b(note.customer) else "unregistered"
        _add(
            credit_notes[key],
            taxable=note.subtotal - note.discount_total,
            cgst=note.cgst_total,
            sgst=note.sgst_total,
            igst=note.igst_total,
            cess=note.cess_total,
        )

    return {
        "period": {"from": date_from, "to": date_to},
        "b2b": dict(b2b),                    # Table 4A
        "b2c_large": b2c_large,              # Table 5
        "exports": dict(exports),            # Table 6A
        "b2c_small": dict(b2c_small),        # Table 7
        "nil_exempt": nil_exempt,            # Table 8
        "credit_debit_notes": dict(credit_notes),  # Table 9B
        "hsn_summary": {                     # Table 12, bifurcated
            "b2b": {f"{hsn}|{rate}": bucket for (hsn, rate), bucket in hsn_b2b.items()},
            "b2c": {f"{hsn}|{rate}": bucket for (hsn, rate), bucket in hsn_b2c.items()},
        },
    }


def get_gstr3b_summary(*, organization, date_from, date_to) -> dict:
    """The GSTR-3B figures: 3.1 outward and RCM, 3.2 inter-State to
    unregistered persons, 4 eligible ITC.

    Note 3.1(d) - inward supplies liable to reverse charge - is an OUTWARD
    liability computed from BILLS. That is not a mistake: under s.5(3)/(4) the
    recipient's purchase creates the tax liability, so it belongs in the
    outward section even though the document is a purchase.
    """
    outward_taxable = _zero_bucket()
    outward_zero_rated = _zero_bucket()
    outward_nil_exempt = _zero_bucket()
    inward_reverse_charge = _zero_bucket()
    itc_available = _zero_bucket()
    itc_reverse_charge = _zero_bucket()
    inter_state_unregistered = defaultdict(_zero_bucket)

    for invoice in _live_invoices(organization, date_from, date_to):
        amounts = {
            "taxable": invoice.subtotal - invoice.discount_total,
            "cgst": invoice.cgst_total,
            "sgst": invoice.sgst_total,
            "igst": invoice.igst_total,
            "cess": invoice.cess_total,
        }
        if invoice.supply_nature in {
            SupplyNature.EXPORT_WITH_TAX,
            SupplyNature.EXPORT_WITHOUT_TAX,
            SupplyNature.SEZ_WITH_TAX,
            SupplyNature.SEZ_WITHOUT_TAX,
        }:
            _add(outward_zero_rated, **amounts)
        elif invoice.tax_total == ZERO and invoice.supply_nature != SupplyNature.UNSPECIFIED:
            _add(outward_nil_exempt, **amounts)
        else:
            _add(outward_taxable, **amounts)

        if invoice.supply_nature == SupplyNature.INTER_STATE and not _is_b2b(invoice.customer):
            _add(inter_state_unregistered[invoice.place_of_supply_id or ""], **amounts)

    # Credit notes reduce the outward figures - 3.1(a) is net of them.
    for note in _live_credit_notes(organization, date_from, date_to):
        _add(
            outward_taxable,
            taxable=-(note.subtotal - note.discount_total),
            cgst=-note.cgst_total,
            sgst=-note.sgst_total,
            igst=-note.igst_total,
            cess=-note.cess_total,
            count=0,
        )

    for bill in _live_bills(organization, date_from, date_to):
        amounts = {
            "taxable": bill.subtotal - bill.discount_total,
            "cgst": bill.cgst_total,
            "sgst": bill.sgst_total,
            "igst": bill.igst_total,
            "cess": bill.cess_total,
        }
        _add(itc_available, **amounts)
        if bill.is_reverse_charge:
            # Counted twice on purpose: as a liability in 3.1(d) and as a
            # credit in 4(A)(3). That is exactly what reverse charge does.
            _add(inward_reverse_charge, **amounts)
            _add(itc_reverse_charge, **amounts)

    return {
        "period": {"from": date_from, "to": date_to},
        "outward": {
            "taxable": outward_taxable,                 # 3.1(a)
            "zero_rated": outward_zero_rated,           # 3.1(b)
            "nil_exempt": outward_nil_exempt,           # 3.1(c)
            "inward_reverse_charge": inward_reverse_charge,  # 3.1(d)
        },
        "inter_state_unregistered": dict(inter_state_unregistered),  # 3.2
        "itc": {
            "all_other": itc_available,                 # 4(A)(5)
            "reverse_charge": itc_reverse_charge,       # 4(A)(3)
        },
    }
