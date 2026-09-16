"""E-way bill validity arithmetic and payload shape.

The validity rule is the one piece of arithmetic here that comes straight from
the official documentation - one day per 200 km, part thereof adding another -
and the boundary cases are where an off-by-one costs a consignment its
validity on the road.
"""

import datetime
from decimal import Decimal

from django.test import SimpleTestCase

from compliance.services.ewaybill_payload import (
    MAX_DISTANCE_KM,
    build_ewb_payload,
    compute_validity,
    requires_ewaybill,
)
from compliance.tests.base import ComplianceTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context

NOON = datetime.datetime(2026, 4, 10, 12, 0, tzinfo=datetime.timezone.utc)


class ValidityTests(SimpleTestCase):
    def _days(self, distance):
        valid_until = compute_validity(distance_km=distance, generated_at=NOON)
        # Validity runs to the END of the final day, so the span from the start
        # of the generation day is the day count.
        start_of_day = datetime.datetime(2026, 4, 10, tzinfo=datetime.timezone.utc)
        return (valid_until - start_of_day).days

    def test_one_day_per_200_km(self):
        self.assertEqual(self._days(1), 1)
        self.assertEqual(self._days(200), 1)

    def test_part_of_a_further_200_km_adds_a_day(self):
        # The boundary that matters: 200 is one day, 201 is two.
        self.assertEqual(self._days(201), 2)
        self.assertEqual(self._days(400), 2)
        self.assertEqual(self._days(401), 3)

    def test_zero_distance_still_gets_one_day(self):
        self.assertEqual(self._days(0), 1)

    def test_long_haul(self):
        self.assertEqual(self._days(4000), 20)

    def test_validity_runs_to_the_end_of_the_day_not_the_hour(self):
        # A bill generated at noon with one day's validity runs to the end of
        # the following day, not to noon the next.
        valid_until = compute_validity(distance_km=100, generated_at=NOON)
        self.assertEqual(valid_until.hour, 0)
        self.assertEqual(valid_until.date(), datetime.date(2026, 4, 11))

    def test_distance_beyond_the_api_ceiling_is_refused(self):
        with self.assertRaises(ApplicationError) as ctx:
            compute_validity(distance_km=MAX_DISTANCE_KM + 1, generated_at=NOON)
        self.assertEqual(ctx.exception.get_codes(), "ewaybill_distance_too_far")

    def test_negative_distance_is_refused(self):
        with self.assertRaises(ApplicationError) as ctx:
            compute_validity(distance_km=-1, generated_at=NOON)
        self.assertEqual(ctx.exception.get_codes(), "ewaybill_distance_invalid")


class ThresholdTests(ComplianceTestsBase):
    def test_threshold_comes_from_configuration_not_a_constant(self):
        with tenant_context(organization_id=self.org_a.id):
            self.assertFalse(
                requires_ewaybill(organization=self.org_a, consignment_value=Decimal("49999.99"))
            )
            self.assertTrue(
                requires_ewaybill(organization=self.org_a, consignment_value=Decimal("50000.00"))
            )

            # Several states set a different figure for intra-State movement,
            # which is exactly why this is a profile field.
            self.profile.ewaybill_threshold = Decimal("100000.00")
            self.profile.save(update_fields=["ewaybill_threshold", "updated_at"])
            self.assertFalse(
                requires_ewaybill(organization=self.org_a, consignment_value=Decimal("50000.00"))
            )

    def test_an_organization_with_no_profile_gets_the_all_india_default(self):
        with tenant_context(organization_id=self.org_b.id):
            self.assertTrue(
                requires_ewaybill(organization=self.org_b, consignment_value=Decimal("50000.00"))
            )


class PayloadTests(ComplianceTestsBase):
    def test_invoice_payload_carries_the_api_field_names(self):
        invoice = self.make_invoice(self.customer_ka, unit_price=Decimal("60000.00"))
        with tenant_context(organization_id=self.org_a.id):
            payload = build_ewb_payload(
                document=invoice, distance_km=850, vehicle_number="MH12AB1234",
                transporter_name="Blue Dart",
            )

        self.assertEqual(payload["supplyType"], "O")
        self.assertEqual(payload["docType"], "INV")
        self.assertEqual(payload["docNo"], invoice.invoice_number)
        self.assertEqual(payload["docDate"], "10/04/2026")
        self.assertEqual(payload["fromStateCode"], 27)
        self.assertEqual(payload["toStateCode"], 29)
        self.assertEqual(payload["transDistance"], "850")
        self.assertEqual(payload["vehicleNo"], "MH12AB1234")
        self.assertEqual(payload["vehicleType"], "R")
        self.assertEqual(payload["igstValue"], 10800.0)
        self.assertEqual(len(payload["itemList"]), 1)
        self.assertEqual(payload["itemList"][0]["hsnCode"], "998311")

    def test_vehicle_type_is_omitted_without_a_vehicle_number(self):
        # vehicleType is only meaningful alongside a number; sending it alone
        # is noise the API does not want.
        invoice = self.make_invoice(self.customer_ka)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_ewb_payload(document=invoice, distance_km=100)
        self.assertNotIn("vehicleType", payload)
        self.assertNotIn("vehicleNo", payload)

    def test_unregistered_buyer_is_reported_as_urp(self):
        invoice = self.make_invoice(self.consumer)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_ewb_payload(document=invoice, distance_km=50)
        self.assertEqual(payload["toGstin"], "URP")

    def test_distance_beyond_the_ceiling_is_refused(self):
        invoice = self.make_invoice(self.customer_ka)
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            build_ewb_payload(document=invoice, distance_km=4001)
        self.assertEqual(ctx.exception.get_codes(), "ewaybill_distance_too_far")

    def test_a_draft_invoice_has_no_number_to_reference(self):
        invoice = self.make_invoice(self.customer_ka, post=False)
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            build_ewb_payload(document=invoice, distance_km=100)
        self.assertEqual(ctx.exception.get_codes(), "document_not_numbered")

    def test_an_unconfigured_organization_cannot_build_a_payload(self):
        invoice = self.make_invoice(self.customer_ka)
        with tenant_context(organization_id=self.org_a.id):
            self.profile.gstin = ""
            self.profile.save(update_fields=["gstin", "updated_at"])
            with self.assertRaises(ApplicationError) as ctx:
                build_ewb_payload(document=invoice, distance_km=100)
            self.assertEqual(ctx.exception.get_codes(), "tax_profile_incomplete")
