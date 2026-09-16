"""Read-side helpers for the tax engine.

Nothing here computes an authoritative amount - `services/computation.py` does
that. These only resolve configuration.
"""

from tax.enums import TaxDirection
from tax.models import StateCode, TaxAccountMapping, TaxProfile, TaxRate


def known_state_codes() -> list[str]:
    """Every valid GST state code. Passed into
    `services/validation.validate_gstin` so that function can stay pure."""
    return list(StateCode.objects.values_list("code", flat=True))


def get_tax_profile(*, organization) -> TaxProfile | None:
    """The organization's tax profile, or None if it has never been configured.

    None is a legitimate answer, not an error: an organization that does no GST
    business never needs one, and every caller degrades to the pre-Phase-7
    behaviour when it is absent.
    """
    return TaxProfile.objects.filter(organization=organization).first()


def get_supplier_state(*, organization) -> StateCode | None:
    profile = get_tax_profile(organization=organization)
    return profile.state if profile else None


def get_tax_account_map(*, organization, direction: str) -> dict:
    """{component: Account} for one direction, in a single query.

    Returned as a plain dict so the posting services can ask for a component
    and fall back on a miss without a second round trip per line.
    """
    return {
        mapping.component: mapping.account
        for mapping in TaxAccountMapping.objects.filter(
            organization=organization, direction=direction
        ).select_related("account")
    }


def get_output_tax_accounts(*, organization) -> dict:
    return get_tax_account_map(organization=organization, direction=TaxDirection.OUTPUT)


def get_input_tax_accounts(*, organization) -> dict:
    return get_tax_account_map(organization=organization, direction=TaxDirection.INPUT)


def resolve_rate_for_date(*, organization, name: str, on_date):
    """The active rate with this name effective on `on_date`, or None.

    Names are unique per organization, so this resolves at most one row; the
    effective window is a filter on that row rather than a way to have two rows
    share a name. An organization whose 18% rate becomes 5% creates a second
    rate and closes the first one's window.
    """
    return next(
        (
            rate
            for rate in TaxRate.objects.filter(organization=organization, is_active=True, name=name)
            if rate.is_effective_on(on_date)
        ),
        None,
    )


def active_rates(*, organization, on_date=None) -> list[TaxRate]:
    rates = TaxRate.objects.filter(organization=organization, is_active=True)
    if on_date is None:
        return list(rates)
    return [rate for rate in rates if rate.is_effective_on(on_date)]
