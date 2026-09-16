from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class LinkedEntityType(models.TextChoices):
    """Every value here must have a matching resolver in
    `documents/services/links.py::ENTITY_RESOLVERS` — see that module for why
    this is a validated (type, id) pair rather than a real cross-app FK or a
    Django GenericForeignKey (documents/CLAUDE.md, phase section 8)."""

    INVOICE = "invoice", "Invoice"
    SALES_ORDER = "sales_order", "Sales Order"
    CUSTOMER = "customer", "Customer"
    BILL = "bill", "Bill"
    PURCHASE_ORDER = "purchase_order", "Purchase Order"
    VENDOR = "vendor", "Vendor"
    EXPENSE = "expense", "Expense"
    BANK_TRANSACTION = "bank_transaction", "Bank Transaction"
    PROJECT = "project", "Project"
    JOURNAL_ENTRY = "journal_entry", "Journal Entry"
    EINVOICE = "einvoice", "e-Invoice"
    EWAYBILL = "ewaybill", "e-Way Bill"


class DocumentLink(TenantScopedModel):
    """Links a Document to a business record in another module. The link is
    tenant-safe by construction: `services/links.py::create_link` resolves
    `entity_id` through the target module's OWN tenant-scoped manager under
    the caller's tenant context, so a cross-organization id simply does not
    resolve — the same fail-closed guarantee `TenantManager` gives everywhere
    else, without `documents` importing every other app's models at this
    (model) layer.
    """

    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="links")
    entity_type = models.CharField(max_length=30, choices=LinkedEntityType.choices)
    entity_id = models.UUIDField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "entity_type", "entity_id"], name="uniq_document_link"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "entity_type", "entity_id"]),
        ]

    def __str__(self):
        return f"{self.document_id} -> {self.entity_type}:{self.entity_id}"
