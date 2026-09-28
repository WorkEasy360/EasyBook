import datetime

from django.db import connection, transaction

from accounts.models import FiscalYear
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError

# A typo guard, not a compliance rule: it stops "2026-04-01 → 2028-03-31"
# from silently becoming one two-year period. A first fiscal year may be
# longer than twelve months in some jurisdictions, so the cap is generous.
MAX_FISCAL_YEAR_MONTHS = 18


def get_fiscal_year_for_date(*, organization, posting_date) -> FiscalYear:
    fiscal_year = FiscalYear.objects.filter(
        organization=organization, start_date__lte=posting_date, end_date__gte=posting_date
    ).first()
    if fiscal_year is None:
        raise ApplicationError(
            "No fiscal year covers this posting date.", code="fiscal_year_not_found"
        )
    return fiscal_year


def assert_period_open(fiscal_year: FiscalYear) -> None:
    if fiscal_year.is_closed:
        raise ApplicationError(
            "This fiscal period is closed to new postings.", code="fiscal_period_closed"
        )


def _first_day_months_after(date: datetime.date, months: int) -> datetime.date:
    index = date.month - 1 + months
    return datetime.date(date.year + index // 12, index % 12 + 1, 1)


@transaction.atomic
def create_fiscal_year(*, organization, start_date, end_date, actor=None) -> tuple[FiscalYear, bool]:
    """The only path that creates a FiscalYear. Returns (fiscal_year, created).

    - Idempotent: the exact same range again returns the existing row
      (created=False) — a double-submitted onboarding form is harmless.
    - Refuses a range that overlaps any existing year of the organization, so
      get_fiscal_year_for_date can never be ambiguous.
    - Concurrency-safe: a per-organization transaction-scoped advisory lock
      serializes the check-then-insert, so two simultaneous setup requests
      cannot both pass the overlap check.
    - Audited like every other financial-configuration mutation.
    """
    if end_date <= start_date:
        raise ApplicationError("The fiscal year must end after it starts.", code="fiscal_year_invalid_range")
    if end_date >= _first_day_months_after(start_date, MAX_FISCAL_YEAR_MONTHS):
        raise ApplicationError(
            f"A fiscal year cannot be longer than {MAX_FISCAL_YEAR_MONTHS} months.", code="fiscal_year_too_long"
        )

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [f"fiscal_year:{organization.id}"])

    existing = FiscalYear.objects.filter(organization=organization, start_date=start_date, end_date=end_date).first()
    if existing is not None:
        return existing, False
    if FiscalYear.objects.filter(organization=organization, start_date__lte=end_date, end_date__gte=start_date).exists():
        raise ApplicationError(
            "This period overlaps an existing fiscal year.", code="fiscal_year_overlap", status_code=409
        )

    fiscal_year = FiscalYear.objects.create(organization=organization, start_date=start_date, end_date=end_date)
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="accounts.FiscalYear",
        object_id=fiscal_year.id,
        changes={"start_date": start_date, "end_date": end_date},
    )
    return fiscal_year, True


def current_fiscal_year(*, organization, today: datetime.date) -> FiscalYear | None:
    return FiscalYear.objects.filter(organization=organization, start_date__lte=today, end_date__gte=today).first()
