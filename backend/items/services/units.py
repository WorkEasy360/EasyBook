from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from items.models.unit import UnitOfMeasure

DEFAULT_UNITS = [
    ("EA", "Each", "ea"),
    ("PC", "Piece", "pc"),
    ("BOX", "Box", "box"),
    ("KG", "Kilogram", "kg"),
    ("G", "Gram", "g"),
    ("L", "Litre", "l"),
    ("M", "Metre", "m"),
    ("HR", "Hour", "hr"),
    ("DAY", "Day", "day"),
]

SYSTEM_UNIT_MUTABLE_FIELDS = {"name", "symbol"}


@transaction.atomic
def create_unit(*, organization, code: str, name: str, symbol: str = "", is_system: bool = False, actor=None) -> UnitOfMeasure:
    unit = UnitOfMeasure.objects.create(
        organization=organization, code=code, name=name, symbol=symbol, is_system=is_system
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="items.UnitOfMeasure",
        object_id=unit.id,
        changes={"code": code, "name": name},
    )
    return unit


@transaction.atomic
def update_unit(*, unit: UnitOfMeasure, actor=None, **fields) -> UnitOfMeasure:
    if unit.is_system:
        disallowed = set(fields) - SYSTEM_UNIT_MUTABLE_FIELDS
        if disallowed:
            raise ApplicationError(
                f"System unit fields cannot be changed: {', '.join(sorted(disallowed))}.",
                code="system_unit_protected",
            )

    changes = {k: v for k, v in fields.items() if getattr(unit, k) != v}
    if not changes:
        return unit
    for field, value in changes.items():
        setattr(unit, field, value)
    unit.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=unit.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="items.UnitOfMeasure",
        object_id=unit.id,
        changes=changes,
    )
    return unit


@transaction.atomic
def seed_default_units(*, organization, actor=None) -> list[UnitOfMeasure]:
    """Creates the standard unit set for an organization. Not wired to
    organization creation automatically (that flow lives in accounts, a
    Phase 0 app this phase does not modify) — call explicitly, e.g. from a
    management command or onboarding step."""
    created = []
    for code, name, symbol in DEFAULT_UNITS:
        if UnitOfMeasure.objects.filter(organization=organization, code=code).exists():
            continue
        created.append(create_unit(organization=organization, code=code, name=name, symbol=symbol, is_system=True, actor=actor))
    return created
