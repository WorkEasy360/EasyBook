from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from tax.models import WithholdingSection

SECTION_MUTABLE_FIELDS = {
    "name", "kind", "applies_to", "rate", "threshold_amount", "account", "is_active",
}


@transaction.atomic
def create_withholding_section(*, organization, code: str, kind: str, applies_to: str,
                               rate, account, name: str = "", threshold_amount=None,
                               actor=None) -> WithholdingSection:
    if account.organization_id != organization.id:
        raise ApplicationError(
            "Withholding account must belong to the same organization.", code="cross_org_reference"
        )

    section = WithholdingSection(
        organization=organization,
        code=code,
        name=name,
        kind=kind,
        applies_to=applies_to,
        rate=rate,
        account=account,
    )
    if threshold_amount is not None:
        section.threshold_amount = threshold_amount
    section.full_clean(exclude=["organization", "account"])
    section.save()
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="tax.WithholdingSection",
        object_id=section.id,
        changes={"code": code, "kind": kind, "rate": rate},
    )
    return section


@transaction.atomic
def update_withholding_section(*, section: WithholdingSection, actor=None, **fields):
    unknown = set(fields) - SECTION_MUTABLE_FIELDS
    if unknown:
        raise ApplicationError(
            f"Unknown withholding section fields: {', '.join(sorted(unknown))}.",
            code="withholding_field_unknown",
        )
    if "account" in fields and fields["account"].organization_id != section.organization_id:
        raise ApplicationError(
            "Withholding account must belong to the same organization.", code="cross_org_reference"
        )

    changes = {k: v for k, v in fields.items() if getattr(section, k) != v}
    if not changes:
        return section
    for field, value in changes.items():
        setattr(section, field, value)
    section.full_clean(exclude=["organization", "account"])
    section.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=section.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="tax.WithholdingSection",
        object_id=section.id,
        changes={k: str(v) for k, v in changes.items()},
    )
    return section
