from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from tax.models import StateCode, TaxAccountMapping, TaxProfile
from tax.selectors import known_state_codes
from tax.services.validation import validate_gstin

PROFILE_MUTABLE_FIELDS = {
    "registration_type",
    "gstin",
    "state",
    "composition_rate",
    "einvoice_enabled",
    "einvoice_reporting_window_days",
    "ewaybill_threshold",
}


def _resolve_state(state_code) -> StateCode | None:
    if state_code is None or state_code == "":
        return None
    if isinstance(state_code, StateCode):
        return state_code
    state = StateCode.objects.filter(code=str(state_code)).first()
    if state is None:
        raise ApplicationError(
            f"'{state_code}' is not a known GST state code.", code="state_code_unknown"
        )
    return state


def _check_gstin_matches_state(gstin: str, state: StateCode | None) -> None:
    """A GSTIN encodes its own state in the first two digits. If both are given
    and they disagree, one of them is a typo - and silently trusting either
    would make every subsequent place-of-supply comparison wrong."""
    if not gstin or state is None:
        return
    if gstin[:2] != state.code:
        raise ApplicationError(
            f"GSTIN state code '{gstin[:2]}' does not match the configured state "
            f"'{state.code} {state.name}'.",
            code="gstin_state_mismatch",
        )


@transaction.atomic
def set_tax_profile(*, organization, actor=None, state_code=None, **fields) -> TaxProfile:
    """Creates or updates the organization's single tax profile.

    Upsert rather than separate create/update because there is exactly one row
    per organization and callers should not have to know whether it exists yet -
    the same shape `inventory` uses for its settings row.
    """
    unknown = set(fields) - PROFILE_MUTABLE_FIELDS
    if unknown:
        raise ApplicationError(
            f"Unknown tax profile fields: {', '.join(sorted(unknown))}.",
            code="tax_profile_field_unknown",
        )

    if "gstin" in fields:
        fields["gstin"] = validate_gstin(fields["gstin"], known_state_codes=known_state_codes())

    if state_code is not None:
        fields["state"] = _resolve_state(state_code)

    profile = TaxProfile.objects.filter(organization=organization).first()
    if profile is None:
        profile = TaxProfile(organization=organization)
        for field, value in fields.items():
            setattr(profile, field, value)
        _check_gstin_matches_state(profile.gstin, profile.state)
        profile.save()
        record_audit(
            organization_id=organization.id,
            actor=actor,
            action=AuditLog.Action.CREATE,
            object_type="tax.TaxProfile",
            object_id=profile.id,
            changes={k: str(v) for k, v in fields.items()},
        )
        return profile

    changes = {k: v for k, v in fields.items() if getattr(profile, k) != v}
    if not changes:
        return profile
    for field, value in changes.items():
        setattr(profile, field, value)
    _check_gstin_matches_state(profile.gstin, profile.state)
    profile.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="tax.TaxProfile",
        object_id=profile.id,
        changes={k: str(v) for k, v in changes.items()},
    )
    return profile


@transaction.atomic
def set_tax_account_mapping(*, organization, component: str, direction: str, account, actor=None):
    """Points one (component, direction) pair at a GL account."""
    if account.organization_id != organization.id:
        raise ApplicationError(
            "Tax account must belong to the same organization.", code="cross_org_reference"
        )

    mapping, created = TaxAccountMapping.objects.update_or_create(
        organization=organization,
        component=component,
        direction=direction,
        defaults={"account": account},
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        object_type="tax.TaxAccountMapping",
        object_id=mapping.id,
        changes={"component": component, "direction": direction, "account": str(account.id)},
    )
    return mapping
