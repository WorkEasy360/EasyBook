"""The configuration services: tax profile, account mappings, rates, sections."""

from datetime import date
from decimal import Decimal

from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from tax.enums import TaxComponent, TaxDirection, WithholdingAppliesTo, WithholdingKind
from tax.models import TaxAccountMapping, TaxProfile, TaxRate
from tax.selectors import (
    active_rates,
    get_output_tax_accounts,
    get_supplier_state,
    get_tax_profile,
    resolve_rate_for_date,
)
from tax.services.profile import set_tax_account_mapping, set_tax_profile
from tax.services.rates import archive_tax_rate, create_tax_rate, update_tax_rate
from tax.services.withholding import create_withholding_section, update_withholding_section
from tax.tests.base import TaxTestsBase


class TaxProfileTests(TaxTestsBase):
    def test_fixture_profile_is_created_once_and_updated_in_place(self):
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(TaxProfile.objects.count(), 1)
            again = set_tax_profile(organization=self.org_a, einvoice_enabled=True)
            self.assertEqual(TaxProfile.objects.count(), 1)
            self.assertEqual(again.id, self.profile.id)
            self.assertTrue(again.einvoice_enabled)

    def test_gstin_is_validated_and_normalized(self):
        with tenant_context(organization_id=self.org_a.id):
            profile = set_tax_profile(
                organization=self.org_a, state_code="29", gstin="29aagcb7383j1z4"
            )
        self.assertEqual(profile.gstin, "29AAGCB7383J1Z4")

    def test_invalid_gstin_rejected(self):
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            set_tax_profile(organization=self.org_a, gstin="27AAPFU0939F1ZA")
        self.assertEqual(ctx.exception.get_codes(), "gstin_checksum_invalid")

    def test_gstin_state_must_agree_with_configured_state(self):
        # The GSTIN carries its own state in the first two digits. Trusting
        # either side silently would make every later place-of-supply
        # comparison wrong.
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            set_tax_profile(
                organization=self.org_a, state_code="29", gstin="27AAPFU0939F1ZV"
            )
        self.assertEqual(ctx.exception.get_codes(), "gstin_state_mismatch")

    def test_unknown_state_code_rejected(self):
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            set_tax_profile(organization=self.org_a, state_code="28")
        self.assertEqual(ctx.exception.get_codes(), "state_code_unknown")

    def test_unknown_field_rejected(self):
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            set_tax_profile(organization=self.org_a, gst_rate=18)
        self.assertEqual(ctx.exception.get_codes(), "tax_profile_field_unknown")

    def test_selectors_read_the_profile(self):
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(get_tax_profile(organization=self.org_a).id, self.profile.id)
            self.assertEqual(get_supplier_state(organization=self.org_a).code, "27")

    def test_absent_profile_reads_as_none_not_an_error(self):
        # An organization doing no GST business never configures one, and every
        # caller must degrade to pre-Phase-7 behaviour rather than blow up.
        with tenant_context(organization_id=self.org_b.id):
            self.assertIsNone(get_tax_profile(organization=self.org_b))
            self.assertIsNone(get_supplier_state(organization=self.org_b))

    def test_thresholds_default_to_the_all_india_baseline(self):
        self.assertEqual(self.profile.ewaybill_threshold, Decimal("50000.00"))
        self.assertIsNone(self.profile.einvoice_reporting_window_days)


class TaxAccountMappingTests(TaxTestsBase):
    def test_mapping_is_upserted_not_duplicated(self):
        self.map_output_accounts()
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(TaxAccountMapping.objects.count(), 4)
            set_tax_account_mapping(
                organization=self.org_a,
                component=TaxComponent.CGST,
                direction=TaxDirection.OUTPUT,
                account=self.output_igst,
            )
            self.assertEqual(TaxAccountMapping.objects.count(), 4)
            accounts = get_output_tax_accounts(organization=self.org_a)
            self.assertEqual(accounts[TaxComponent.CGST].id, self.output_igst.id)

    def test_output_and_input_directions_are_independent(self):
        self.map_output_accounts()
        self.map_input_accounts()
        with tenant_context(organization_id=self.org_a.id):
            output = get_output_tax_accounts(organization=self.org_a)
            self.assertEqual(output[TaxComponent.CGST].id, self.output_cgst.id)
            from tax.selectors import get_input_tax_accounts

            self.assertEqual(
                get_input_tax_accounts(organization=self.org_a)[TaxComponent.CGST].id,
                self.input_cgst.id,
            )

    def test_unmapped_organization_reads_as_an_empty_dict(self):
        # Absence must stay cheap: it is what makes every pre-Phase-7
        # organization keep posting through the document-level fallback.
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(get_output_tax_accounts(organization=self.org_a), {})

    def test_cross_org_account_rejected(self):
        with tenant_context(organization_id=self.org_b.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            set_tax_account_mapping(
                organization=self.org_b,
                component=TaxComponent.CGST,
                direction=TaxDirection.OUTPUT,
                account=self.output_cgst,
            )
        self.assertEqual(ctx.exception.get_codes(), "cross_org_reference")


class TaxRateTests(TaxTestsBase):
    def test_create_and_resolve_by_date(self):
        with tenant_context(organization_id=self.org_a.id):
            create_tax_rate(
                organization=self.org_a, name="GST 18%", rate=Decimal("18"),
                effective_from=date(2025, 9, 22),
            )
            self.assertIsNotNone(
                resolve_rate_for_date(
                    organization=self.org_a, name="GST 18%", on_date=date(2026, 4, 1)
                )
            )
            # Before the window opens the rate does not resolve - which is the
            # whole point of effective dating.
            self.assertIsNone(
                resolve_rate_for_date(
                    organization=self.org_a, name="GST 18%", on_date=date(2025, 9, 21)
                )
            )

    def test_closed_window_stops_resolving(self):
        with tenant_context(organization_id=self.org_a.id):
            rate = create_tax_rate(
                organization=self.org_a, name="GST 12%", rate=Decimal("12"),
                effective_from=date(2017, 7, 1), effective_to=date(2025, 9, 21),
            )
            self.assertTrue(rate.is_effective_on(date(2020, 1, 1)))
            self.assertFalse(rate.is_effective_on(date(2026, 1, 1)))

    def test_no_slab_whitelist_the_40_percent_demerit_rate_is_accepted(self):
        # The slabs changed twice in the last year; a choices list or check
        # constraint naming today's would have to migrate with every
        # notification.
        with tenant_context(organization_id=self.org_a.id):
            rate = create_tax_rate(
                organization=self.org_a, name="GST 40%", rate=Decimal("40")
            )
            self.assertEqual(rate.rate, Decimal("40"))

    def test_cess_is_separate_from_the_gst_rate(self):
        with tenant_context(organization_id=self.org_a.id):
            rate = create_tax_rate(
                organization=self.org_a, name="GST 28% + cess",
                rate=Decimal("28"), cess_rate=Decimal("12"),
            )
            self.assertEqual(rate.cess_rate, Decimal("12"))

    def test_duplicate_name_per_org_rejected(self):
        from django.db import IntegrityError

        with tenant_context(organization_id=self.org_a.id):
            create_tax_rate(organization=self.org_a, name="GST 5%", rate=Decimal("5"))
            with self.assertRaises(IntegrityError):
                TaxRate.objects.create(
                    organization=self.org_a, name="GST 5%", rate=Decimal("5")
                )

    def test_negative_rate_rejected(self):
        from django.core.exceptions import ValidationError

        with tenant_context(organization_id=self.org_a.id), self.assertRaises(ValidationError):
            create_tax_rate(organization=self.org_a, name="Bad", rate=Decimal("-1"))

    def test_archive_rather_than_delete(self):
        with tenant_context(organization_id=self.org_a.id):
            rate = create_tax_rate(organization=self.org_a, name="GST 5%", rate=Decimal("5"))
            archive_tax_rate(tax_rate=rate)
            rate.refresh_from_db()
            self.assertFalse(rate.is_active)
            self.assertEqual(active_rates(organization=self.org_a), [])

    def test_unknown_update_field_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            rate = create_tax_rate(organization=self.org_a, name="GST 5%", rate=Decimal("5"))
            with self.assertRaises(ApplicationError) as ctx:
                update_tax_rate(tax_rate=rate, organization=self.org_b)
            self.assertEqual(ctx.exception.get_codes(), "tax_rate_field_unknown")


class WithholdingSectionTests(TaxTestsBase):
    def test_create_a_tds_section(self):
        with tenant_context(organization_id=self.org_a.id):
            section = create_withholding_section(
                organization=self.org_a, code="194Q", name="Purchase of goods",
                kind=WithholdingKind.TDS, applies_to=WithholdingAppliesTo.PURCHASE,
                rate=Decimal("0.10"), account=self.tds_account,
            )
        self.assertEqual(section.rate, Decimal("0.10"))
        self.assertEqual(section.kind, WithholdingKind.TDS)

    def test_rate_above_100_rejected(self):
        from django.core.exceptions import ValidationError

        with tenant_context(organization_id=self.org_a.id), self.assertRaises(ValidationError):
            create_withholding_section(
                organization=self.org_a, code="BAD", kind=WithholdingKind.TDS,
                applies_to=WithholdingAppliesTo.PURCHASE, rate=Decimal("101"),
                account=self.tds_account,
            )

    def test_cross_org_account_rejected(self):
        with tenant_context(organization_id=self.org_b.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            create_withholding_section(
                organization=self.org_b, code="194Q", kind=WithholdingKind.TDS,
                applies_to=WithholdingAppliesTo.PURCHASE, rate=Decimal("0.10"),
                account=self.tds_account,
            )
        self.assertEqual(ctx.exception.get_codes(), "cross_org_reference")

    def test_update_rejects_unknown_field(self):
        with tenant_context(organization_id=self.org_a.id):
            section = create_withholding_section(
                organization=self.org_a, code="194Q", kind=WithholdingKind.TDS,
                applies_to=WithholdingAppliesTo.PURCHASE, rate=Decimal("0.10"),
                account=self.tds_account,
            )
            with self.assertRaises(ApplicationError) as ctx:
                update_withholding_section(section=section, code="194C")
            self.assertEqual(ctx.exception.get_codes(), "withholding_field_unknown")
