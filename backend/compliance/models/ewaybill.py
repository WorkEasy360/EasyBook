from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import TenantScopedModel


class EWayBillStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    GENERATED = "generated", "Generated"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class TransportMode(models.TextChoices):
    """The NIC e-Way Bill API's `transMode` values."""

    ROAD = "1", "Road"
    RAIL = "2", "Rail"
    AIR = "3", "Air"
    SHIP = "4", "Ship"


class VehicleType(models.TextChoices):
    """`vehicleType` in the Generate EWB v1.03 payload."""

    REGULAR = "R", "Regular"
    OVER_DIMENSIONAL_CARGO = "O", "Over-dimensional cargo"


class EWayBill(TenantScopedModel):
    """An e-way bill against an invoice or a delivery challan.

    A challan is a first-class target, not an afterthought: goods move on
    dispatch, which is exactly when `sales.DeliveryChallan` exists and the
    invoice often does not yet.

    `valid_until` is STORED rather than derived on read. It is computed from
    the distance at generation time by
    `services/ewaybill_payload.compute_validity`, but what governs the
    consignment is the validity the portal actually issued - recomputing it
    later from a distance someone has since edited would quietly move an
    expiry date that a vehicle on the road is relying on.
    """

    invoice = models.ForeignKey(
        "sales.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="ewaybills"
    )
    delivery_challan = models.ForeignKey(
        "sales.DeliveryChallan", null=True, blank=True, on_delete=models.PROTECT,
        related_name="ewaybills",
    )

    status = models.CharField(
        max_length=16, choices=EWayBillStatus.choices, default=EWayBillStatus.PENDING
    )
    provider_key = models.CharField(max_length=32, default="manual")

    # --- as returned by the portal ----------------------------------------
    ewb_no = models.CharField(max_length=16, blank=True)
    ewb_date = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    # --- transport details ------------------------------------------------
    distance_km = models.PositiveIntegerField(default=0)
    transport_mode = models.CharField(
        max_length=2, choices=TransportMode.choices, default=TransportMode.ROAD
    )
    transporter_gstin = models.CharField(max_length=15, blank=True)
    transporter_name = models.CharField(max_length=128, blank=True)
    transport_doc_no = models.CharField(max_length=32, blank=True)
    vehicle_number = models.CharField(max_length=16, blank=True)
    vehicle_type = models.CharField(
        max_length=2, choices=VehicleType.choices, default=VehicleType.REGULAR
    )
    consignment_value = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0")
    )

    payload = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    error_message = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    _PORTAL_REPORTED_FIELDS = ("ewb_no", "ewb_date", "valid_until")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(invoice__isnull=False, delivery_challan__isnull=True)
                    | models.Q(invoice__isnull=True, delivery_challan__isnull=False)
                ),
                name="ewaybill_targets_exactly_one_document",
            ),
            models.UniqueConstraint(
                fields=["organization", "ewb_no"],
                condition=~models.Q(ewb_no=""),
                name="uniq_ewaybill_number_per_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "invoice"],
                condition=models.Q(invoice__isnull=False) & ~models.Q(status="cancelled"),
                name="uniq_live_ewaybill_per_invoice",
            ),
            models.UniqueConstraint(
                fields=["organization", "delivery_challan"],
                condition=models.Q(delivery_challan__isnull=False) & ~models.Q(status="cancelled"),
                name="uniq_live_ewaybill_per_challan",
            ),
            # 4000 km is the API's own ceiling (Generate EWB v1.03).
            models.CheckConstraint(
                condition=models.Q(distance_km__lte=4000),
                name="ewaybill_distance_within_api_limit",
            ),
        ]
        indexes = [models.Index(fields=["organization", "status"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"EWB {self.ewb_no or '(pending)'}"

    @property
    def document(self):
        return self.invoice or self.delivery_challan

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values(*self._PORTAL_REPORTED_FIELDS)
                .first()
            )
            if previous:
                changed = [
                    field
                    for field in self._PORTAL_REPORTED_FIELDS
                    if previous[field] not in ("", None) and previous[field] != getattr(self, field)
                ]
                if changed:
                    raise ValueError(
                        "Portal-reported e-way bill fields are immutable once set: "
                        f"{', '.join(changed)}."
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "E-way bill records are never deleted - cancel the bill instead, so the "
            "cancellation stays on the record."
        )
