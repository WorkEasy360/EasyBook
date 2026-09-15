from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from inventory.models.warehouse import Warehouse


@transaction.atomic
def create_warehouse(*, organization, code: str, name: str, address: str = "", is_default: bool = False, actor=None) -> Warehouse:
    if is_default:
        Warehouse.objects.filter(organization=organization, is_default=True).update(is_default=False)

    warehouse = Warehouse.objects.create(
        organization=organization, code=code, name=name, address=address, is_default=is_default
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="inventory.Warehouse",
        object_id=warehouse.id,
        changes={"code": code, "name": name, "is_default": is_default},
    )
    return warehouse


@transaction.atomic
def update_warehouse(*, warehouse: Warehouse, actor=None, **fields) -> Warehouse:
    if fields.get("is_default") is True:
        Warehouse.objects.filter(organization=warehouse.organization_id, is_default=True).exclude(
            pk=warehouse.pk
        ).update(is_default=False)

    changes = {k: v for k, v in fields.items() if getattr(warehouse, k) != v}
    if not changes:
        return warehouse
    for field, value in changes.items():
        setattr(warehouse, field, value)
    warehouse.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=warehouse.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="inventory.Warehouse",
        object_id=warehouse.id,
        changes=changes,
    )
    return warehouse


def archive_warehouse(*, warehouse: Warehouse, actor=None) -> Warehouse:
    return update_warehouse(warehouse=warehouse, is_active=False, is_default=False, actor=actor)
