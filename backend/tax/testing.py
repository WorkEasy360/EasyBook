"""Test-support helpers for anything that depends on the state-code master.

Lives in app code rather than under `tests/` because `sales`, `purchases` and
`compliance` test suites need it too, and a cross-app import into another app's
test package is worse than a small public helper here.

WHY THIS EXISTS: the master is seeded by a data migration, but
`TransactionTestCase` flushes every table between tests and does NOT re-run data
migrations. The first transaction test in a run therefore truncates
`tax_statecode` for every test that follows it, in every app. Calling
`ensure_state_codes()` from a fixture makes that self-healing instead of a
cross-suite ordering landmine.
"""

from tax.models import StateCode
from tax.state_master import STATE_CODES


def ensure_state_codes() -> None:
    """Restores the state master if a flush has removed it. Cheap no-op when
    the rows are already present."""
    if StateCode.objects.exists():
        return
    StateCode.objects.bulk_create(
        [
            StateCode(
                code=code,
                name=name,
                is_union_territory=is_ut,
                uses_utgst=uses_utgst,
                is_special=is_special,
            )
            for code, name, is_ut, uses_utgst, is_special in STATE_CODES
        ],
        ignore_conflicts=True,
    )
