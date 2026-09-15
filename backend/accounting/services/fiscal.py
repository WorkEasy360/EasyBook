from accounts.models import FiscalYear
from core.exceptions import ApplicationError


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
