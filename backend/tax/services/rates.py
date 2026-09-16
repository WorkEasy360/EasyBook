from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from tax.models import TaxRate

RATE_MUTABLE_FIELDS = {"name", "rate", "cess_rate", "is_active", "effective_from", "effective_to"}


@transaction.atomic
def create_tax_rate(*, organization, name: str, rate, cess_rate=None, effective_from=None,
                    effective_to=None, actor=None) -> TaxRate:
    tax_rate = TaxRate(
        organization=organization,
        name=name,
        rate=rate,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    if cess_rate is not None:
        tax_rate.cess_rate = cess_rate
    tax_rate.full_clean(exclude=["organization"])
    tax_rate.save()
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="tax.TaxRate",
        object_id=tax_rate.id,
        changes={"name": name, "rate": rate, "cess_rate": tax_rate.cess_rate},
    )
    return tax_rate


@transaction.atomic
def update_tax_rate(*, tax_rate: TaxRate, actor=None, **fields) -> TaxRate:
    """Rates are editable, unlike posted documents: a rate is configuration,
    and every document that used one already snapshotted the number onto its
    own lines (see the `tax_rate` column on each line model). Editing this row
    cannot reach back into a posted journal."""
    unknown = set(fields) - RATE_MUTABLE_FIELDS
    if unknown:
        raise ApplicationError(
            f"Unknown tax rate fields: {', '.join(sorted(unknown))}.", code="tax_rate_field_unknown"
        )

    changes = {k: v for k, v in fields.items() if getattr(tax_rate, k) != v}
    if not changes:
        return tax_rate
    for field, value in changes.items():
        setattr(tax_rate, field, value)
    tax_rate.full_clean(exclude=["organization"])
    tax_rate.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=tax_rate.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="tax.TaxRate",
        object_id=tax_rate.id,
        changes={k: str(v) for k, v in changes.items()},
    )
    return tax_rate


@transaction.atomic
def archive_tax_rate(*, tax_rate: TaxRate, actor=None) -> TaxRate:
    """No hard delete - the house rule across items and accounting alike. A
    rate referenced by a historical document must remain resolvable."""
    return update_tax_rate(tax_rate=tax_rate, is_active=False, actor=actor)
