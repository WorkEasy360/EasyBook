import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.api.serializers import (
    AccountSerializer,
    FiscalYearCreateSerializer,
    FiscalYearSerializer,
    JournalEntryCreateSerializer,
    JournalEntryLinesUpdateSerializer,
    JournalEntryReverseSerializer,
    JournalEntrySerializer,
)
from accounting.models.account import Account
from accounting.models.journal import JournalEntry, JournalStatus
from accounting.selectors import get_account_running_ledger, get_trial_balance
from accounting.services.fiscal import create_fiscal_year, current_fiscal_year
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from authz.permissions import HasOrgPermission
from authz.roles import Permission, role_has_permission
from core.exceptions import ApplicationError
from core.views import OrganizationScopedMixin


class AccountListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = AccountSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_ACCOUNTING if self.request.method == "GET" else Permission.MANAGE_ACCOUNTING

    def get_queryset(self):
        qs = Account.objects.all()
        account_type = self.request.query_params.get("account_type")
        if account_type:
            qs = qs.filter(account_type=account_type)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs


class AccountDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = AccountSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_ACCOUNTING if self.request.method == "GET" else Permission.MANAGE_ACCOUNTING

    def get_queryset(self):
        # NOT a class-level `queryset = Account.objects.all()` attribute:
        # that expression evaluates once at import time, before any request
        # sets tenant context, so TenantManager would permanently bake in
        # `.none()`. See accounting/CLAUDE.md / core/CLAUDE.md.
        return Account.objects.all()


class JournalEntryListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_TRANSACTIONS if self.request.method == "GET" else Permission.MANAGE_TRANSACTIONS

    def get_serializer_class(self):
        return JournalEntryCreateSerializer if self.request.method == "POST" else JournalEntrySerializer

    def get_queryset(self):
        qs = JournalEntry.objects.prefetch_related("lines")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        journal = serializer.save()
        return Response(JournalEntrySerializer(journal).data, status=201)


class JournalEntryDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = JournalEntrySerializer

    def get_queryset(self):
        return JournalEntry.objects.prefetch_related("lines")

    @property
    def required_permission(self):
        return Permission.VIEW_TRANSACTIONS if self.request.method == "GET" else Permission.MANAGE_TRANSACTIONS

    def update(self, request, *args, **kwargs):
        journal = self.get_object()
        if journal.status != JournalStatus.DRAFT:
            raise ApplicationError("Only draft journal entries can be modified.", code="journal_not_draft")

        if "lines" in request.data:
            lines_serializer = JournalEntryLinesUpdateSerializer(
                data={"lines": request.data["lines"]}, context={"journal": journal}
            )
            lines_serializer.is_valid(raise_exception=True)
            lines_serializer.save()

        header_data = {k: v for k, v in request.data.items() if k in ("reference", "posting_date", "memo")}
        if header_data:
            header_serializer = self.get_serializer(journal, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        journal.refresh_from_db()
        return Response(JournalEntrySerializer(journal).data)


class JournalEntryPostView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_TRANSACTIONS

    def post(self, request, pk):
        journal = post_journal(journal_id=pk, organization=request.organization, actor=request.user)
        return Response(JournalEntrySerializer(journal).data)


class JournalEntryReverseView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_TRANSACTIONS

    def post(self, request, pk):
        journal = JournalEntry.objects.filter(pk=pk).first()
        if journal is None:
            raise ApplicationError("Journal entry not found.", code="journal_not_found", status_code=404)
        serializer = JournalEntryReverseSerializer(
            data=request.data, context={"request": request, "journal": journal}
        )
        serializer.is_valid(raise_exception=True)
        reversal = serializer.save()
        return Response(JournalEntrySerializer(reversal).data, status=201)


class AccountLedgerView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_TRANSACTIONS

    def get(self, request, pk):
        try:
            account = Account.objects.get(pk=pk)
        except Account.DoesNotExist:
            raise ApplicationError("Account not found.", code="account_not_found", status_code=404)

        from_date = _parse_date(request.query_params.get("from_date"))
        to_date = _parse_date(request.query_params.get("to_date"))
        ledger = get_account_running_ledger(account=account, from_date=from_date, to_date=to_date)

        return Response(
            {
                "account": AccountSerializer(account).data,
                "opening_balance": str(ledger["opening_balance"]),
                "closing_balance": str(ledger["closing_balance"]),
                "entries": [
                    {
                        "journal_entry_id": entry["journal_entry"].id,
                        "journal_number": entry["journal_entry"].journal_number,
                        "posting_date": entry["journal_entry"].posting_date,
                        "description": entry["line"].description,
                        "debit": str(entry["debit"]),
                        "credit": str(entry["credit"]),
                        "running_balance": str(entry["running_balance"]),
                    }
                    for entry in ledger["entries"]
                ],
            }
        )


class TrialBalanceView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_REPORTS

    def get(self, request):
        as_of_date = _parse_date(request.query_params.get("as_of_date")) or datetime.date.today()
        from_date = _parse_date(request.query_params.get("from_date"))

        result = get_trial_balance(organization=request.organization, as_of_date=as_of_date, from_date=from_date)

        return Response(
            {
                "as_of_date": as_of_date,
                "from_date": from_date,
                "is_balanced": result["is_balanced"],
                "total_period_debit": str(result["total_period_debit"]),
                "total_period_credit": str(result["total_period_credit"]),
                "total_closing_debit": str(result["total_closing_debit"]),
                "total_closing_credit": str(result["total_closing_credit"]),
                "rows": [
                    {
                        "account": AccountSerializer(row["account"]).data,
                        "opening_balance": str(row["opening_balance"]),
                        "period_debit": str(row["period_debit"]),
                        "period_credit": str(row["period_credit"]),
                        "closing_debit": str(row["closing_debit"]),
                        "closing_credit": str(row["closing_credit"]),
                    }
                    for row in result["rows"]
                ],
            }
        )


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ApplicationError(f"Invalid date '{value}', expected YYYY-MM-DD.", code="invalid_date")


def _organization_today(organization) -> datetime.date:
    try:
        zone = ZoneInfo(organization.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return timezone.localdate(timezone=zone)


class FiscalYearListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    """GET lists the organization's fiscal years; POST creates one through
    accounting.services.fiscal.create_fiscal_year (201 created, 200 when the
    identical range already exists)."""

    permission_classes = [HasOrgPermission]
    serializer_class = FiscalYearSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_ACCOUNTING if self.request.method == "GET" else Permission.MANAGE_ACCOUNTING

    def get_queryset(self):
        return FiscalYear.objects.all()

    def post(self, request):
        serializer = FiscalYearCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fiscal_year, created = create_fiscal_year(
            organization=request.organization, actor=request.user, **serializer.validated_data
        )
        return Response(FiscalYearSerializer(fiscal_year).data, status=201 if created else 200)


class FiscalYearSetupStatusView(OrganizationScopedMixin, APIView):
    """Whether the active organization is ready for day-to-day accounting.

    Open to every member (no role permission): the frontend asks this on every
    authenticated page to decide whether to send the user to fiscal-year
    setup, and it reveals only the organization's own setup state."""

    def get(self, request):
        organization = request.organization
        current = current_fiscal_year(organization=organization, today=_organization_today(organization))
        return Response(
            {
                "has_current_fiscal_year": current is not None,
                "current": FiscalYearSerializer(current).data if current else None,
                "fiscal_year_start_month": organization.fiscal_year_start_month,
                "can_manage": role_has_permission(request.membership.role, Permission.MANAGE_ACCOUNTING),
            }
        )
