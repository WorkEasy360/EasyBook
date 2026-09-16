"""The INV-01 payload, and the IRN lifecycle.

The payload assertions pin the NIC vocabulary - `Version` "1.1", the `SupTyp`
values, `DocDtls.Typ`, the DD/MM/YYYY date format - because those are the parts
that cannot be inferred from the codebase and would fail only at a real filing.
"""

import datetime
from decimal import Decimal

from compliance.models import EInvoiceDocument, EInvoiceStatus
from compliance.providers.base import (
    available_einvoice_providers,
    get_einvoice_provider,
)
from compliance.services.einvoice import (
    generate_einvoice,
    prepare_einvoice,
    record_irn,
    record_irn_cancellation,
)
from compliance.services.einvoice_payload import SCHEMA_VERSION, build_irn_payload
from compliance.tests.base import ComplianceTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from sales.services.credit_notes import create_credit_note, issue_credit_note
from tax.enums import SupplyNature, SupplyType

SAMPLE_IRN = "a" * 64


class PayloadTests(ComplianceTestsBase):
    def test_payload_matches_the_verified_schema_shape(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_irn_payload(invoice)

        self.assertEqual(payload["Version"], SCHEMA_VERSION)
        self.assertEqual(payload["Version"], "1.1")
        self.assertEqual(payload["TranDtls"]["TaxSch"], "GST")
        self.assertEqual(payload["TranDtls"]["SupTyp"], SupplyType.B2B)
        self.assertEqual(payload["TranDtls"]["RegRev"], "N")
        self.assertEqual(payload["DocDtls"]["Typ"], "INV")
        self.assertEqual(payload["DocDtls"]["No"], invoice.invoice_number)
        # The schema wants DD/MM/YYYY, not ISO.
        self.assertEqual(payload["DocDtls"]["Dt"], "10/04/2026")
        self.assertEqual(payload["SellerDtls"]["Gstin"], "27AAPFU0939F1ZV")
        self.assertEqual(payload["BuyerDtls"]["Pos"], "27")

    def test_value_block_agrees_with_the_document(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_irn_payload(invoice)

        self.assertEqual(payload["ValDtls"]["AssVal"], 1000.0)
        self.assertEqual(payload["ValDtls"]["CgstVal"], 90.0)
        self.assertEqual(payload["ValDtls"]["SgstVal"], 90.0)
        self.assertEqual(payload["ValDtls"]["IgstVal"], 0.0)
        self.assertEqual(payload["ValDtls"]["TotInvVal"], float(invoice.total))

    def test_item_list_carries_the_component_split_per_line(self):
        invoice = self.make_invoice(self.customer_ka)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_irn_payload(invoice)

        item = payload["ItemList"][0]
        self.assertEqual(item["SlNo"], "1")
        self.assertEqual(item["IsServc"], "Y")
        self.assertEqual(item["HsnCd"], "998311")
        self.assertEqual(item["GstRt"], 18.0)
        self.assertEqual(item["IgstAmt"], 180.0)
        self.assertEqual(item["CgstAmt"], 0.0)

    def test_igst_on_intra_flag_is_set_for_an_sez_supply_in_the_same_state(self):
        # The schema has a dedicated flag for exactly the s.8 case the state
        # codes alone cannot express: same state, but IGST is due.
        from sales.services.customers import create_customer
        from tax.enums import TaxTreatment

        with tenant_context(organization_id=self.org_a.id):
            sez = create_customer(
                organization=self.org_a, customer_code="SEZ", display_name="SEZ Unit",
                currency=self.currency, tax_treatment=TaxTreatment.SEZ,
                place_of_supply_state_code="27",
            )
        invoice = self.make_invoice(sez)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_irn_payload(invoice)

        self.assertEqual(invoice.supply_nature, SupplyNature.SEZ_WITH_TAX)
        self.assertEqual(payload["TranDtls"]["SupTyp"], SupplyType.SEZWP)
        self.assertEqual(payload["TranDtls"]["IgstOnIntra"], "Y")

    def test_ordinary_intra_state_supply_does_not_set_the_flag(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_irn_payload(invoice)
        self.assertEqual(payload["TranDtls"]["IgstOnIntra"], "N")

    def test_reverse_charge_sets_regrev(self):
        invoice = self.make_invoice(self.customer_mh, is_reverse_charge=True)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_irn_payload(invoice)
        self.assertEqual(payload["TranDtls"]["RegRev"], "Y")

    def test_unregistered_buyer_is_reported_as_urp(self):
        invoice = self.make_invoice(self.consumer)
        with tenant_context(organization_id=self.org_a.id):
            payload = build_irn_payload(invoice)
        self.assertEqual(payload["BuyerDtls"]["Gstin"], "URP")

    def test_a_draft_invoice_cannot_be_reported(self):
        invoice = self.make_invoice(self.customer_mh, post=False)
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            build_irn_payload(invoice)
        self.assertEqual(ctx.exception.get_codes(), "document_not_numbered")

    def test_an_undetermined_supply_cannot_be_reported(self):
        # UNSPECIFIED is ours, not NIC's - it must never reach a payload.
        invoice = self.make_invoice(self.customer_mh, post=False)
        with tenant_context(organization_id=self.org_a.id):
            invoice.supply_type = SupplyType.UNSPECIFIED
            invoice.save(update_fields=["supply_type", "updated_at"])
            with self.assertRaises(ApplicationError) as ctx:
                build_irn_payload(invoice)
            self.assertEqual(ctx.exception.get_codes(), "supply_type_not_reportable")

    def test_a_credit_note_reports_as_crn(self):
        from compliance.services.einvoice_payload import DOC_TYPE_CREDIT_NOTE

        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            from accounting.models.account import AccountType
            from accounting.services.accounts import create_account

            unapplied = create_account(
                organization=self.org_a, code="2200", name="Customer Advances",
                account_type=AccountType.LIABILITY,
            )
            note = create_credit_note(
                organization=self.org_a, customer=self.customer_mh,
                credit_note_date=datetime.date(2026, 4, 12), source_invoice=invoice,
                receivable_account=self.ar_account, unapplied_credit_account=unapplied,
                lines=[{
                    "item": self.service, "quantity": Decimal("1"),
                    "unit_price": Decimal("1000.00"), "tax_rate": Decimal("18"),
                }],
            )
            note = issue_credit_note(credit_note_id=note.id, organization=self.org_a)
            payload = build_irn_payload(note, doc_type=DOC_TYPE_CREDIT_NOTE)

        self.assertEqual(payload["DocDtls"]["Typ"], "CRN")
        self.assertEqual(payload["DocDtls"]["No"], note.credit_note_number)


class IrnLifecycleTests(ComplianceTestsBase):
    def test_prepare_stores_the_payload_without_reporting(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record = prepare_einvoice(document=invoice, actor=self.user_a)

        self.assertEqual(record.status, EInvoiceStatus.PENDING)
        self.assertEqual(record.irn, "")
        self.assertEqual(record.payload["Version"], "1.1")

    def test_recording_an_irn_marks_it_generated(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record = record_irn(
                document=invoice, irn=SAMPLE_IRN, ack_no="112010000123",
                signed_qr_code="eyJ...", actor=self.user_a,
            )

        self.assertEqual(record.status, EInvoiceStatus.GENERATED)
        self.assertEqual(record.irn, SAMPLE_IRN)
        self.assertIsNotNone(record.ack_date)

    def test_recording_the_same_irn_twice_is_idempotent(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            first = record_irn(document=invoice, irn=SAMPLE_IRN)
            second = record_irn(document=invoice, irn=SAMPLE_IRN)
            self.assertEqual(first.id, second.id)
            self.assertEqual(EInvoiceDocument.objects.count(), 1)

    def test_a_second_different_irn_is_refused(self):
        # A duplicate IRN is not a row to clean up later - it is a second
        # government filing against one invoice.
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record_irn(document=invoice, irn=SAMPLE_IRN)
            with self.assertRaises(ApplicationError) as ctx:
                record_irn(document=invoice, irn="b" * 64)
            self.assertEqual(ctx.exception.get_codes(), "einvoice_already_generated")

    def test_irp_reported_fields_are_immutable_once_set(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record = record_irn(document=invoice, irn=SAMPLE_IRN, ack_no="112010000123")
            record.irn = "c" * 64
            with self.assertRaises(ValueError):
                record.save()

    def test_records_are_never_deleted(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record = record_irn(document=invoice, irn=SAMPLE_IRN)
            with self.assertRaises(ValueError):
                record.delete()

    def test_cancellation_keeps_the_record(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record_irn(document=invoice, irn=SAMPLE_IRN)
            cancelled = record_irn_cancellation(
                document=invoice, reason="Wrong customer", actor=self.user_a
            )
            self.assertEqual(cancelled.status, EInvoiceStatus.CANCELLED)
            self.assertEqual(cancelled.irn, SAMPLE_IRN)
            self.assertIsNotNone(cancelled.cancelled_at)
            # A cancelled filing is itself a fact the audit trail needs.
            self.assertEqual(EInvoiceDocument.all_objects.count(), 1)

    def test_cancellation_is_idempotent(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record_irn(document=invoice, irn=SAMPLE_IRN)
            first = record_irn_cancellation(document=invoice, reason="x")
            second = record_irn_cancellation(document=invoice, reason="x")
            self.assertEqual(first.id, second.id)

    def test_cancelling_when_nothing_was_reported_is_a_404(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            record_irn_cancellation(document=invoice, reason="x")
        self.assertEqual(ctx.exception.get_codes(), "einvoice_not_found")

    def test_a_fresh_attempt_is_allowed_after_cancellation(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record_irn(document=invoice, irn=SAMPLE_IRN)
            record_irn_cancellation(document=invoice, reason="x")
            again = record_irn(document=invoice, irn="d" * 64)
            self.assertEqual(again.status, EInvoiceStatus.GENERATED)
            self.assertEqual(EInvoiceDocument.objects.count(), 2)


class ProviderTests(ComplianceTestsBase):
    def test_only_the_manual_provider_is_registered(self):
        # No NIC client ships: root CLAUDE.md rule 5, and there are no
        # credentials here to test one against.
        self.assertEqual(available_einvoice_providers(), ["manual"])

    def test_the_manual_provider_does_not_claim_to_generate(self):
        provider = get_einvoice_provider("manual")
        self.assertFalse(provider.supports_generate())

    def test_generating_through_the_manual_provider_says_so_plainly(self):
        # "Not integrated" must never look like "filed".
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            generate_einvoice(document=invoice)
        self.assertEqual(ctx.exception.get_codes(), "provider_cannot_generate")

    def test_an_unknown_provider_is_refused(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id), self.assertRaises(
            ApplicationError
        ) as ctx:
            generate_einvoice(document=invoice, provider_key="nic")
        self.assertEqual(ctx.exception.get_codes(), "provider_unknown")
