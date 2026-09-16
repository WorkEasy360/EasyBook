import uuid

from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from documents.models.link import DocumentLink, LinkedEntityType
from documents.services.links import create_link, delete_link
from documents.services.uploads import upload_document
from purchases.services.bills import create_bill
from purchases.tests.base import DUE_DATE, ORDER_DATE, PurchasesTestsBase
from sales.services.customers import create_customer

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class DocumentLinkTests(PurchasesTestsBase):
    def setUp(self):
        super().setUp()
        with tenant_context(organization_id=self.org_a.id):
            self.document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="vendor-bill.pdf",
            )
            self.bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                lines=self._service_lines(), payable_account=self.ap_account,
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme Co",
                currency=self.currency,
            )

    def test_links_same_tenant_vendor(self):
        with tenant_context(organization_id=self.org_a.id):
            link = create_link(
                document=self.document, entity_type=LinkedEntityType.VENDOR, entity_id=self.vendor.id,
                actor=self.user_a,
            )
        self.assertEqual(link.entity_type, LinkedEntityType.VENDOR)

    def test_links_same_tenant_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            link = create_link(
                document=self.document, entity_type=LinkedEntityType.BILL, entity_id=self.bill.id,
                actor=self.user_a,
            )
        self.assertEqual(link.entity_type, LinkedEntityType.BILL)

    def test_links_same_tenant_customer(self):
        with tenant_context(organization_id=self.org_a.id):
            link = create_link(
                document=self.document, entity_type=LinkedEntityType.CUSTOMER, entity_id=self.customer.id,
                actor=self.user_a,
            )
        self.assertEqual(link.entity_type, LinkedEntityType.CUSTOMER)

    def test_cross_tenant_link_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_link(
                    document=self.document, entity_type=LinkedEntityType.VENDOR, entity_id=self.vendor_b.id,
                    actor=self.user_a,
                )
        self.assertEqual(ctx.exception.get_codes(), "entity_not_found")

    def test_nonexistent_entity_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_link(
                    document=self.document, entity_type=LinkedEntityType.VENDOR, entity_id=uuid.uuid4(),
                    actor=self.user_a,
                )
        self.assertEqual(ctx.exception.get_codes(), "entity_not_found")

    def test_unsupported_entity_type_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_link(document=self.document, entity_type="not_a_real_type", entity_id=uuid.uuid4())
        self.assertEqual(ctx.exception.get_codes(), "unsupported_entity_type")

    def test_duplicate_link_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            create_link(document=self.document, entity_type=LinkedEntityType.VENDOR, entity_id=self.vendor.id)
            create_link(document=self.document, entity_type=LinkedEntityType.VENDOR, entity_id=self.vendor.id)
            count = DocumentLink.objects.filter(document=self.document, entity_type=LinkedEntityType.VENDOR).count()
        self.assertEqual(count, 1)

    def test_delete_link(self):
        with tenant_context(organization_id=self.org_a.id):
            link = create_link(document=self.document, entity_type=LinkedEntityType.VENDOR, entity_id=self.vendor.id)
            delete_link(link=link, actor=self.user_a)
            self.assertFalse(DocumentLink.objects.filter(pk=link.pk).exists())
