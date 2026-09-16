"""Component splitting, with the cent invariant as the centrepiece.

`cgst + sgst + igst + cess == tax_amount` is not a rounding nicety: each
component posts to its own GL account while the customer is debited the
document total, so a cent lost between the halves is a journal that will not
balance and a document that cannot post at all.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from core.exceptions import ApplicationError
from core.money import calculate_line
from tax.enums import SupplyNature
from tax.services.computation import component_totals, compute_withholding, split_tax

ZERO = Decimal("0")


class SplitTaxTests(SimpleTestCase):
    """No database needed - splitting is pure arithmetic over Decimals."""

    def test_intra_state_splits_in_half(self):
        result = split_tax(
            taxable_amount=Decimal("1000.00"),
            tax_rate=Decimal("18"),
            supply_nature=SupplyNature.INTRA_STATE,
        )
        self.assertEqual(result["cgst"], Decimal("90.00"))
        self.assertEqual(result["sgst"], Decimal("90.00"))
        self.assertEqual(result["igst"], ZERO)
        self.assertEqual(result["tax_amount"], Decimal("180.00"))

    def test_inter_state_is_all_igst(self):
        result = split_tax(
            taxable_amount=Decimal("1000.00"),
            tax_rate=Decimal("18"),
            supply_nature=SupplyNature.INTER_STATE,
        )
        self.assertEqual(result["igst"], Decimal("180.00"))
        self.assertEqual(result["cgst"], ZERO)
        self.assertEqual(result["sgst"], ZERO)

    def test_halves_always_resum_to_the_total_across_awkward_amounts(self):
        # The invariant, hammered. Independent rounding of each half fails
        # here: 101.00 at 5% gives 2.525 twice, which rounds to 2.53 + 2.53 =
        # 5.06 against a tax_amount of 5.05.
        rates = [
            Decimal("5"), Decimal("12"), Decimal("18"), Decimal("28"),
            Decimal("40"), Decimal("0.25"), Decimal("3"), Decimal("1.5"),
        ]
        amounts = [
            Decimal(f"{whole}.{frac:02d}")
            for whole in (1, 7, 99, 101, 333, 1000, 12345)
            for frac in (0, 1, 5, 33, 49, 50, 51, 99)
        ]
        for rate in rates:
            for amount in amounts:
                with self.subTest(rate=rate, amount=amount):
                    result = split_tax(
                        taxable_amount=amount,
                        tax_rate=rate,
                        supply_nature=SupplyNature.INTRA_STATE,
                    )
                    self.assertEqual(
                        result["cgst"] + result["sgst"] + result["igst"] + result["cess"],
                        result["tax_amount"],
                    )

    def test_the_known_drift_case_explicitly(self):
        result = split_tax(
            taxable_amount=Decimal("101.00"),
            tax_rate=Decimal("5"),
            supply_nature=SupplyNature.INTRA_STATE,
        )
        self.assertEqual(result["tax_amount"], Decimal("5.05"))
        self.assertEqual(result["cgst"] + result["sgst"], Decimal("5.05"))
        # The remainder half carries the odd cent rather than both halves
        # rounding up.
        self.assertEqual(result["cgst"], Decimal("2.53"))
        self.assertEqual(result["sgst"], Decimal("2.52"))

    def test_split_agrees_with_core_money_on_the_total(self):
        # The two must never be computed from different bases: core.money owns
        # "how much tax", this module owns "of which, how much is which".
        for rate in (Decimal("5"), Decimal("18"), Decimal("40")):
            for amount in (Decimal("33.33"), Decimal("777.77"), Decimal("1.01")):
                with self.subTest(rate=rate, amount=amount):
                    line = calculate_line(
                        quantity=Decimal("1"), unit_price=amount, tax_rate=rate
                    )
                    split = split_tax(
                        taxable_amount=line["taxable_amount"],
                        tax_rate=rate,
                        supply_nature=SupplyNature.INTRA_STATE,
                    )
                    self.assertEqual(split["tax_amount"], line["tax_amount"])

    def test_cess_is_added_on_top_of_gst_not_inside_it(self):
        result = split_tax(
            taxable_amount=Decimal("1000.00"),
            tax_rate=Decimal("18"),
            cess_rate=Decimal("12"),
            supply_nature=SupplyNature.INTER_STATE,
        )
        self.assertEqual(result["igst"], Decimal("180.00"))
        self.assertEqual(result["cess"], Decimal("120.00"))
        self.assertEqual(result["tax_amount"], Decimal("300.00"))

    def test_zero_rated_natures_produce_no_tax_at_all(self):
        for nature in (SupplyNature.EXPORT_WITHOUT_TAX, SupplyNature.SEZ_WITHOUT_TAX):
            with self.subTest(nature=nature):
                result = split_tax(
                    taxable_amount=Decimal("1000.00"),
                    tax_rate=Decimal("18"),
                    cess_rate=Decimal("12"),
                    supply_nature=nature,
                )
                self.assertEqual(result["tax_amount"], ZERO)
                self.assertEqual(result["igst"], ZERO)
                self.assertEqual(result["cess"], ZERO)

    def test_export_with_payment_of_tax_charges_igst(self):
        result = split_tax(
            taxable_amount=Decimal("1000.00"),
            tax_rate=Decimal("18"),
            supply_nature=SupplyNature.EXPORT_WITH_TAX,
        )
        self.assertEqual(result["igst"], Decimal("180.00"))

    def test_unspecified_keeps_the_tax_uncomponented(self):
        # What every document created before this phase reads as: there is
        # tax, but no GST split applies to it. A legitimate resting state.
        result = split_tax(
            taxable_amount=Decimal("1000.00"),
            tax_rate=Decimal("18"),
            supply_nature=SupplyNature.UNSPECIFIED,
        )
        self.assertEqual(result["tax_amount"], Decimal("180.00"))
        self.assertEqual(result["cgst"], ZERO)
        self.assertEqual(result["sgst"], ZERO)
        self.assertEqual(result["igst"], ZERO)

    def test_zero_rate_produces_zero_components(self):
        result = split_tax(
            taxable_amount=Decimal("1000.00"),
            tax_rate=ZERO,
            supply_nature=SupplyNature.INTRA_STATE,
        )
        self.assertEqual(result["tax_amount"], ZERO)
        self.assertEqual(result["cgst"], ZERO)
        self.assertEqual(result["sgst"], ZERO)

    def test_negative_inputs_rejected(self):
        cases = [
            ({"taxable_amount": Decimal("-1")}, "taxable_amount_invalid"),
            ({"tax_rate": Decimal("-1")}, "line_tax_rate_invalid"),
            ({"cess_rate": Decimal("-1")}, "line_cess_rate_invalid"),
        ]
        for override, code in cases:
            base = {
                "taxable_amount": Decimal("100"),
                "tax_rate": Decimal("18"),
                "supply_nature": SupplyNature.INTRA_STATE,
            }
            base.update(override)
            with self.subTest(code=code), self.assertRaises(ApplicationError) as ctx:
                split_tax(**base)
            self.assertEqual(ctx.exception.get_codes(), code)


class ComponentTotalsTests(SimpleTestCase):
    def test_totals_sum_already_rounded_line_values(self):
        rows = [
            {
                "cgst_amount": Decimal("2.53"), "sgst_amount": Decimal("2.52"),
                "igst_amount": ZERO, "cess_amount": ZERO,
            },
            {
                "cgst_amount": Decimal("9.00"), "sgst_amount": Decimal("9.00"),
                "igst_amount": ZERO, "cess_amount": Decimal("1.11"),
            },
        ]
        totals = component_totals(rows)
        self.assertEqual(totals["cgst_total"], Decimal("11.53"))
        self.assertEqual(totals["sgst_total"], Decimal("11.52"))
        self.assertEqual(totals["igst_total"], ZERO)
        self.assertEqual(totals["cess_total"], Decimal("1.11"))

    def test_empty_document_totals_to_zero(self):
        totals = component_totals([])
        self.assertEqual(set(totals.values()), {ZERO})


class _FakeSection:
    def __init__(self, rate, threshold_amount=ZERO):
        self.rate = rate
        self.threshold_amount = threshold_amount


class WithholdingTests(SimpleTestCase):
    def test_no_section_means_no_withholding(self):
        self.assertEqual(compute_withholding(base_amount=Decimal("1000"), section=None), ZERO)

    def test_rate_is_applied_and_rounded(self):
        section = _FakeSection(Decimal("0.10"))
        self.assertEqual(
            compute_withholding(base_amount=Decimal("123456.78"), section=section),
            Decimal("123.46"),
        )

    def test_threshold_is_not_consulted(self):
        # Deliberate: whether a payee has crossed a cumulative annual threshold
        # is a judgement across documents this function cannot see. Returning
        # zero here would under-deduct on the invoice that crosses it.
        section = _FakeSection(Decimal("1"), threshold_amount=Decimal("5000000"))
        self.assertEqual(
            compute_withholding(base_amount=Decimal("100.00"), section=section),
            Decimal("1.00"),
        )

    def test_negative_base_rejected(self):
        with self.assertRaises(ApplicationError) as ctx:
            compute_withholding(base_amount=Decimal("-1"), section=_FakeSection(Decimal("1")))
        self.assertEqual(ctx.exception.get_codes(), "withholding_base_invalid")
