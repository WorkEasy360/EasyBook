from decimal import Decimal

from django.test import SimpleTestCase

from core.exceptions import ApplicationError
from core.money import calculate_document_totals, calculate_line


class CalculateLineTests(SimpleTestCase):
    def test_no_discount_no_tax(self):
        result = calculate_line(quantity=Decimal("2"), unit_price=Decimal("100.00"))
        self.assertEqual(result["line_base"], Decimal("200.00"))
        self.assertEqual(result["discount_amount"], Decimal("0.00"))
        self.assertEqual(result["taxable_amount"], Decimal("200.00"))
        self.assertEqual(result["tax_amount"], Decimal("0.00"))
        self.assertEqual(result["line_total"], Decimal("200.00"))

    def test_discount_then_tax_on_post_discount_amount(self):
        result = calculate_line(
            quantity=Decimal("1"), unit_price=Decimal("100.00"),
            discount_percent=Decimal("10"), tax_rate=Decimal("18"),
        )
        self.assertEqual(result["line_base"], Decimal("100.00"))
        self.assertEqual(result["discount_amount"], Decimal("10.00"))
        self.assertEqual(result["taxable_amount"], Decimal("90.00"))
        self.assertEqual(result["tax_amount"], Decimal("16.20"))
        self.assertEqual(result["line_total"], Decimal("106.20"))

    def test_fractional_quantity_rounds_half_up(self):
        result = calculate_line(quantity=Decimal("1.005"), unit_price=Decimal("10.00"))
        # 1.005 * 10.00 = 10.05 exactly -> no rounding ambiguity here, but
        # confirms 4dp quantity precision flows through to a 2dp money result.
        self.assertEqual(result["line_base"], Decimal("10.05"))

    def test_zero_quantity_rejected(self):
        with self.assertRaises(ApplicationError):
            calculate_line(quantity=Decimal("0"), unit_price=Decimal("10.00"))

    def test_negative_unit_price_rejected(self):
        with self.assertRaises(ApplicationError):
            calculate_line(quantity=Decimal("1"), unit_price=Decimal("-1"))

    def test_discount_percent_over_100_rejected(self):
        with self.assertRaises(ApplicationError):
            calculate_line(quantity=Decimal("1"), unit_price=Decimal("10"), discount_percent=Decimal("101"))

    def test_negative_tax_rate_rejected(self):
        with self.assertRaises(ApplicationError):
            calculate_line(quantity=Decimal("1"), unit_price=Decimal("10"), tax_rate=Decimal("-1"))


class CalculateDocumentTotalsTests(SimpleTestCase):
    def test_sums_lines(self):
        lines = [
            calculate_line(quantity=Decimal("1"), unit_price=Decimal("100"), tax_rate=Decimal("18")),
            calculate_line(quantity=Decimal("2"), unit_price=Decimal("50"), discount_percent=Decimal("10")),
        ]
        totals = calculate_document_totals(lines)
        self.assertEqual(totals["subtotal"], Decimal("200.00"))
        self.assertEqual(totals["discount"], Decimal("10.00"))
        self.assertEqual(totals["tax"], Decimal("18.00"))
        self.assertEqual(totals["total"], Decimal("208.00"))

    def test_empty_lines_yields_zero_totals(self):
        totals = calculate_document_totals([])
        self.assertEqual(totals, {
            "subtotal": Decimal("0"), "discount": Decimal("0"), "tax": Decimal("0"), "total": Decimal("0"),
        })
