"""IGST Act sections 7 and 8, asserted as a matrix.

The case worth the whole file is `test_sez_in_the_same_state_is_still_inter_state`:
a plain `supplier_state == place_of_supply` comparison - the obvious
implementation, and the one a reader might "simplify" this back to - gets it
wrong, charges CGST+SGST on a supply that owes IGST, and files it in the wrong
GSTR-1 table.
"""

from django.test import TestCase

from core.exceptions import ApplicationError
from tax.enums import SupplyNature, SupplyType, TaxTreatment
from tax.models import StateCode
from tax.services.determination import (
    determine_supply_nature,
    is_inter_state,
    is_zero_rated,
    supply_type_for,
)
from tax.testing import ensure_state_codes


class SupplyNatureTests(TestCase):
    def setUp(self):
        ensure_state_codes()
        self.maharashtra = StateCode.objects.get(code="27")
        self.karnataka = StateCode.objects.get(code="29")
        self.chandigarh = StateCode.objects.get(code="04")
        self.other_territory = StateCode.objects.get(code="97")

    def _determine(self, **kwargs):
        kwargs.setdefault("supplier_state", self.maharashtra)
        kwargs.setdefault("place_of_supply", self.maharashtra)
        kwargs.setdefault("party_treatment", TaxTreatment.REGISTERED)
        return determine_supply_nature(**kwargs)

    # --- s.8(1): same state -> intra-State -------------------------------

    def test_same_state_is_intra_state(self):
        self.assertEqual(self._determine(), SupplyNature.INTRA_STATE)
        self.assertFalse(is_inter_state(SupplyNature.INTRA_STATE))

    def test_same_union_territory_is_intra_state(self):
        # A UT supply is still "intra-State" in the s.8 sense; only the label
        # on the state half differs (UTGST rather than SGST).
        self.assertEqual(
            self._determine(supplier_state=self.chandigarh, place_of_supply=self.chandigarh),
            SupplyNature.INTRA_STATE,
        )
        self.assertTrue(self.chandigarh.uses_utgst)
        self.assertEqual(self.chandigarh.state_component_label, "UTGST")
        self.assertEqual(self.maharashtra.state_component_label, "SGST")

    # --- s.7(1): different states -> inter-State --------------------------

    def test_different_states_is_inter_state(self):
        self.assertEqual(
            self._determine(place_of_supply=self.karnataka), SupplyNature.INTER_STATE
        )
        self.assertTrue(is_inter_state(SupplyNature.INTER_STATE))

    def test_state_to_union_territory_is_inter_state(self):
        self.assertEqual(
            self._determine(place_of_supply=self.chandigarh), SupplyNature.INTER_STATE
        )

    # --- s.8 exclusions: the cases a bare == gets wrong -------------------

    def test_sez_in_the_same_state_is_still_inter_state(self):
        # s.8(1) first proviso: a supply to an SEZ developer or unit is
        # inter-State regardless of the states involved.
        nature = self._determine(
            place_of_supply=self.maharashtra, party_treatment=TaxTreatment.SEZ
        )
        self.assertEqual(nature, SupplyNature.SEZ_WITH_TAX)
        self.assertTrue(is_inter_state(nature))

    def test_sez_under_lut_is_zero_rated_but_still_inter_state(self):
        nature = self._determine(
            place_of_supply=self.maharashtra,
            party_treatment=TaxTreatment.SEZ,
            with_payment_of_tax=False,
        )
        self.assertEqual(nature, SupplyNature.SEZ_WITHOUT_TAX)
        self.assertTrue(is_zero_rated(nature))
        self.assertTrue(is_inter_state(nature))

    def test_overseas_party_is_export_and_ignores_state_codes(self):
        # An overseas party has no Indian state; passing a matching one must
        # not turn an export into a local supply.
        self.assertEqual(
            self._determine(
                place_of_supply=self.maharashtra, party_treatment=TaxTreatment.OVERSEAS
            ),
            SupplyNature.EXPORT_WITH_TAX,
        )

    def test_export_under_lut_is_zero_rated(self):
        nature = self._determine(
            party_treatment=TaxTreatment.OVERSEAS, with_payment_of_tax=False
        )
        self.assertEqual(nature, SupplyNature.EXPORT_WITHOUT_TAX)
        self.assertTrue(is_zero_rated(nature))

    def test_deemed_export(self):
        self.assertEqual(
            self._determine(party_treatment=TaxTreatment.DEEMED_EXPORT),
            SupplyNature.DEEMED_EXPORT,
        )

    # --- fails closed ------------------------------------------------------

    def test_missing_supplier_state_refuses_rather_than_guessing(self):
        with self.assertRaises(ApplicationError) as ctx:
            self._determine(supplier_state=None)
        self.assertEqual(ctx.exception.get_codes(), "supplier_state_missing")

    def test_missing_place_of_supply_refuses_rather_than_guessing(self):
        with self.assertRaises(ApplicationError) as ctx:
            self._determine(place_of_supply=None)
        self.assertEqual(ctx.exception.get_codes(), "place_of_supply_missing")

    def test_reporting_bucket_code_cannot_be_a_suppliers_own_state(self):
        # 97 is "Other Territory", a reporting bucket. Left unguarded, a
        # supplier sitting in 97 supplying to 97 would read as intra-State.
        with self.assertRaises(ApplicationError) as ctx:
            self._determine(
                supplier_state=self.other_territory, place_of_supply=self.other_territory
            )
        self.assertEqual(ctx.exception.get_codes(), "supplier_state_invalid")

    def test_unregistered_consumer_in_same_state_is_intra_state(self):
        # B2C changes which GSTR-1 table the supply files in, not which tax
        # applies to it.
        self.assertEqual(
            self._determine(party_treatment=TaxTreatment.CONSUMER), SupplyNature.INTRA_STATE
        )


class SupplyTypeMappingTests(TestCase):
    """The e-Invoice `TranDtls.SupTyp` values are NIC's vocabulary, not ours -
    a value not in this set is an invoice the IRP will reject."""

    def test_each_special_nature_maps_to_its_schema_value(self):
        cases = [
            (SupplyNature.EXPORT_WITH_TAX, SupplyType.EXPWP),
            (SupplyNature.EXPORT_WITHOUT_TAX, SupplyType.EXPWOP),
            (SupplyNature.SEZ_WITH_TAX, SupplyType.SEZWP),
            (SupplyNature.SEZ_WITHOUT_TAX, SupplyType.SEZWOP),
            (SupplyNature.DEEMED_EXPORT, SupplyType.DEXP),
        ]
        for nature, expected in cases:
            with self.subTest(nature=nature):
                self.assertEqual(supply_type_for(nature, TaxTreatment.REGISTERED), expected)

    def test_ordinary_domestic_supply_maps_to_b2b(self):
        self.assertEqual(
            supply_type_for(SupplyNature.INTRA_STATE, TaxTreatment.REGISTERED), SupplyType.B2B
        )
        self.assertEqual(
            supply_type_for(SupplyNature.INTER_STATE, TaxTreatment.REGISTERED), SupplyType.B2B
        )

    def test_unspecified_is_never_a_schema_value(self):
        # UNSPECIFIED is ours, for documents predating this phase. It must
        # never be emitted into a payload.
        self.assertNotIn(
            SupplyType.UNSPECIFIED,
            {SupplyType.B2B, SupplyType.SEZWP, SupplyType.SEZWOP,
             SupplyType.EXPWP, SupplyType.EXPWOP, SupplyType.DEXP},
        )
