from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from items.models.hsn_sac import HsnSacCode


@transaction.atomic
def create_hsn_sac_code(*, organization, code: str, classification: str, description: str = "", actor=None) -> HsnSacCode:
    entry = HsnSacCode.objects.create(
        organization=organization, code=code, classification=classification, description=description
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="items.HsnSacCode",
        object_id=entry.id,
        changes={"code": code, "classification": classification},
    )
    return entry
