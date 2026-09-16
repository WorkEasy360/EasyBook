"""Document <-> business-entity linking (phase section 8).

`entity_id` is a UUID, not a real cross-app foreign key or a Django
GenericForeignKey. Tenant safety comes from resolving it through the target
module's OWN tenant-scoped manager, under the CALLER's ambient tenant
context: a cross-organization id simply fails to resolve, the same
fail-closed guarantee `core.managers.TenantManager` gives every other query
in the system, without `documents` importing sales/purchases/banking/
projects/accounting/compliance models at the model-definition layer (which
would violate the module layering in backend/CLAUDE.md).

Imports are deliberately lazy (inside `_resolvers()`) for the same reason:
`documents` sits ABOVE every module it can link to, exactly like `reports`.
"""

from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from documents.models.document import Document
from documents.models.link import DocumentLink, LinkedEntityType


def _resolvers():
    from accounting.models.journal import JournalEntry
    from banking.models.statement import BankTransaction
    from compliance.models.einvoice import EInvoiceDocument
    from compliance.models.ewaybill import EWayBill
    from projects.models.project import Project
    from purchases.models.bill import Bill
    from purchases.models.expense import Expense
    from purchases.models.purchase_order import PurchaseOrder
    from purchases.models.vendor import Vendor
    from sales.models.customer import Customer
    from sales.models.invoice import Invoice
    from sales.models.sales_order import SalesOrder

    return {
        LinkedEntityType.INVOICE: Invoice,
        LinkedEntityType.SALES_ORDER: SalesOrder,
        LinkedEntityType.CUSTOMER: Customer,
        LinkedEntityType.BILL: Bill,
        LinkedEntityType.PURCHASE_ORDER: PurchaseOrder,
        LinkedEntityType.VENDOR: Vendor,
        LinkedEntityType.EXPENSE: Expense,
        LinkedEntityType.BANK_TRANSACTION: BankTransaction,
        LinkedEntityType.PROJECT: Project,
        LinkedEntityType.JOURNAL_ENTRY: JournalEntry,
        LinkedEntityType.EINVOICE: EInvoiceDocument,
        LinkedEntityType.EWAYBILL: EWayBill,
    }


def _resolve_entity(*, entity_type: str, entity_id) -> None:
    """Raises ApplicationError if `entity_id` does not resolve to a row the
    CURRENT tenant context can see — covers both "does not exist at all" and
    "belongs to a different organization" with the same fail-closed answer."""
    model = _resolvers().get(entity_type)
    if model is None:
        raise ApplicationError(f"Unsupported entity_type: {entity_type}", code="unsupported_entity_type")
    if not model.objects.filter(pk=entity_id).exists():
        raise ApplicationError(
            "Linked entity was not found in this organization.", code="entity_not_found"
        )


@transaction.atomic
def create_link(*, document: Document, entity_type: str, entity_id, actor=None) -> DocumentLink:
    document = Document.objects.get(pk=document.pk)
    _resolve_entity(entity_type=entity_type, entity_id=entity_id)

    link, created = DocumentLink.objects.get_or_create(
        document=document, entity_type=entity_type, entity_id=entity_id,
        defaults={"organization": document.organization, "created_by": actor},
    )
    if created:
        record_audit(
            organization_id=document.organization_id, actor=actor, action=AuditLog.Action.CREATE,
            object_type="documents.DocumentLink", object_id=link.id,
            changes={"document_id": str(document.id), "entity_type": entity_type, "entity_id": str(entity_id)},
        )
    return link


def delete_link(*, link: DocumentLink, actor=None) -> None:
    link = DocumentLink.objects.get(pk=link.pk)
    link_id, document_id, entity_type, entity_id = link.id, link.document_id, link.entity_type, link.entity_id
    organization_id = link.organization_id
    link.delete()
    record_audit(
        organization_id=organization_id, actor=actor, action=AuditLog.Action.DELETE,
        object_type="documents.DocumentLink", object_id=link_id,
        changes={"document_id": str(document_id), "entity_type": entity_type, "entity_id": str(entity_id)},
    )
