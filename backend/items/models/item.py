from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel


class ItemType(models.TextChoices):
    PRODUCT = "product", "Product"
    SERVICE = "service", "Service"
    # COMPOSITE is deliberately not offered yet — nothing in items/inventory
    # implements composite-item behavior; adding the choice without behavior
    # would let a client pick a state that silently does the wrong thing.


class Item(TenantScopedModel):
    """Reusable product/service master data. Owns no stock quantity — that is
    inventory.StockMovement's job once track_inventory is true. See items/CLAUDE.md.
    """

    item_type = models.CharField(max_length=16, choices=ItemType.choices)
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    unit = models.ForeignKey("items.UnitOfMeasure", on_delete=models.PROTECT, related_name="+")

    is_active = models.BooleanField(default=True)
    is_sellable = models.BooleanField(default=True)
    is_purchasable = models.BooleanField(default=True)
    track_inventory = models.BooleanField(default=False)

    sales_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    sales_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    purchase_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    purchase_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    inventory_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cogs_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reorder_level = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))

    hsn_sac_code = models.ForeignKey(
        "items.HsnSacCode", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Freeform label only — no tax-rate table exists; GST rate logic is out of
    # scope for this phase (see root CLAUDE.md: don't invent compliance rules).
    tax_category = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sku"], name="uniq_item_sku_per_org", condition=~models.Q(sku="")
            ),
            models.CheckConstraint(
                check=~(models.Q(item_type=ItemType.SERVICE) & models.Q(track_inventory=True)),
                name="service_item_cannot_track_inventory",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "item_type"]),
            models.Index(fields=["organization", "is_active"]),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name
