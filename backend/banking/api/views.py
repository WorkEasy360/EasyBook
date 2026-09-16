"""Banking API views.

PERMISSION SHAPE. Reading a bank account or a statement line follows the
existing VIEW_ACCOUNTING grant (the General Ledger already discloses every
bank journal, so a stricter tier here would be theatre). The acts that decide
what the books SAY are separated out: IMPORT_BANK_STATEMENT,
MANAGE_BANK_RULES, RECONCILE_BANK — which certifies a balance — and
RECORD_BANK_TRANSFER, which moves money and therefore sits beside
RECORD_VENDOR_PAYMENT on the Accountant side of the segregation-of-duties
line. See authz/roles.py.
"""

from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from authz.permissions import HasOrgPermission
from authz.roles import Permission
from banking.api.serializers import (
    BankAccountCreateSerializer,
    BankAccountSerializer,
    BankAccountSummarySerializer,
    BankAccountUpdateSerializer,
    BankReconciliationCreateSerializer,
    BankReconciliationSerializer,
    BankRuleCreateSerializer,
    BankRuleSerializer,
    BankRuleUpdateSerializer,
    BankTransactionCreateSerializer,
    BankTransactionMatchSerializer,
    BankTransactionSerializer,
    BankTransferCreateSerializer,
    BankTransferSerializer,
    CategorizeTransactionSerializer,
    ConfirmTransferPairSerializer,
    CreateMatchSerializer,
    ExcludeTransactionSerializer,
    ReconciliationSummarySerializer,
    ReopenSerializer,
    StatementImportCreateSerializer,
    StatementImportSerializer,
    TransferCandidateSerializer,
    VoidSerializer,
)
from banking.models.bank_account import BankAccount
from banking.models.match import BankTransactionMatch
from banking.models.reconciliation import BankReconciliation
from banking.models.rule import BankRule
from banking.models.statement import BankTransaction, StatementImport
from banking.models.transfer import BankTransfer
from banking.selectors import get_bank_account_summary, get_reconciliation_summary
from banking.services.matching import (
    auto_match,
    confirm_match,
    find_transfer_counterparts,
    suggest_matches,
    uncategorize_transaction,
    unmatch,
)
from banking.services.reconciliation import (
    abandon_reconciliation,
    complete_reconciliation,
    reopen_reconciliation,
)
from banking.services.rules import apply_rules_to_transaction
from banking.services.transactions import exclude_transaction, restore_transaction
from banking.services.transfers import confirm_detected_transfer, void_transfer
from core.exceptions import ApplicationError
from core.views import OrganizationScopedMixin


def _get_bank_account_or_404(pk) -> BankAccount:
    bank_account = BankAccount.objects.filter(pk=pk).first()
    if bank_account is None:
        raise ApplicationError("Bank account not found.", code="bank_account_not_found", status_code=404)
    return bank_account


def _get_transaction_or_404(pk) -> BankTransaction:
    transaction = BankTransaction.objects.filter(pk=pk).select_related("bank_account").first()
    if transaction is None:
        raise ApplicationError(
            "Bank transaction not found.", code="bank_transaction_not_found", status_code=404
        )
    return transaction


# ------------------------------------------------------- bank accounts


class BankAccountListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return (
            Permission.VIEW_BANK_ACCOUNTS
            if self.request.method == "GET"
            else Permission.MANAGE_BANK_ACCOUNTS
        )

    def get_serializer_class(self):
        return BankAccountCreateSerializer if self.request.method == "POST" else BankAccountSerializer

    def get_queryset(self):
        qs = BankAccount.objects.all()
        kind = self.request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(BankAccountSerializer(serializer.save()).data, status=201)


class BankAccountDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = BankAccountSerializer

    @property
    def required_permission(self):
        return (
            Permission.VIEW_BANK_ACCOUNTS
            if self.request.method == "GET"
            else Permission.MANAGE_BANK_ACCOUNTS
        )

    def get_queryset(self):
        return BankAccount.objects.all()

    def update(self, request, *args, **kwargs):
        bank_account = self.get_object()
        serializer = BankAccountUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "bank_account": bank_account}
        )
        serializer.is_valid(raise_exception=True)
        return Response(BankAccountSerializer(serializer.save()).data)


class BankAccountSummaryView(OrganizationScopedMixin, APIView):
    """Book, statement and cleared balances side by side — see
    banking/selectors.py for why all three exist."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BANK_TRANSACTIONS

    def get(self, request, pk):
        summary = get_bank_account_summary(
            bank_account=_get_bank_account_or_404(pk),
            as_of=request.query_params.get("as_of") or None,
        )
        return Response(BankAccountSummarySerializer(summary).data)


# ---------------------------------------------------------- statements


class StatementImportListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return (
            Permission.VIEW_BANK_TRANSACTIONS
            if self.request.method == "GET"
            else Permission.IMPORT_BANK_STATEMENT
        )

    serializer_class = StatementImportSerializer

    def get_queryset(self):
        return StatementImport.objects.filter(bank_account_id=self.kwargs["pk"])

    def create(self, request, *args, **kwargs):
        serializer = StatementImportCreateSerializer(
            data=request.data,
            context={"request": request, "bank_account": _get_bank_account_or_404(self.kwargs["pk"])},
        )
        serializer.is_valid(raise_exception=True)
        return Response(StatementImportSerializer(serializer.save()).data, status=201)


class BankTransactionListView(OrganizationScopedMixin, generics.ListAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BANK_TRANSACTIONS
    serializer_class = BankTransactionSerializer

    def get_queryset(self):
        qs = BankTransaction.objects.all()
        for param, field in (
            ("bank_account", "bank_account_id"),
            ("status", "status"),
        ):
            value = self.request.query_params.get(param)
            if value:
                qs = qs.filter(**{field: value})
        from_date = self.request.query_params.get("from_date")
        if from_date:
            qs = qs.filter(transaction_date__gte=from_date)
        to_date = self.request.query_params.get("to_date")
        if to_date:
            qs = qs.filter(transaction_date__lte=to_date)
        return qs


class BankTransactionCreateView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.IMPORT_BANK_STATEMENT

    def post(self, request, pk):
        serializer = BankTransactionCreateSerializer(
            data=request.data,
            context={"request": request, "bank_account": _get_bank_account_or_404(pk)},
        )
        serializer.is_valid(raise_exception=True)
        return Response(BankTransactionSerializer(serializer.save()).data, status=201)


class BankTransactionDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BANK_TRANSACTIONS
    serializer_class = BankTransactionSerializer

    def get_queryset(self):
        return BankTransaction.objects.all()


class BankTransactionExcludeView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        serializer = ExcludeTransactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transaction = exclude_transaction(
            transaction_id=pk,
            organization=request.organization,
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(BankTransactionSerializer(transaction).data)


class BankTransactionRestoreView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        transaction = restore_transaction(
            transaction_id=pk, organization=request.organization, actor=request.user
        )
        return Response(BankTransactionSerializer(transaction).data)


# ------------------------------------------------------------- matches


class TransactionSuggestionsView(OrganizationScopedMixin, APIView):
    """Deterministic suggestions. Writes unconfirmed rows; confirms nothing."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        matches = suggest_matches(transaction_id=pk, organization=request.organization)
        return Response(BankTransactionMatchSerializer(matches, many=True).data)

    def get(self, request, pk):
        matches = BankTransactionMatch.objects.filter(transaction_id=pk)
        return Response(BankTransactionMatchSerializer(matches, many=True).data)


class TransactionAutoMatchView(OrganizationScopedMixin, APIView):
    """Confirms the single obvious candidate, or nothing. Never picks between
    two — see services/matching.py::auto_match."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        match = auto_match(transaction_id=pk, organization=request.organization, actor=request.user)
        if match is None:
            return Response({"matched": False, "match": None})
        return Response({"matched": True, "match": BankTransactionMatchSerializer(match).data})


class TransactionMatchCreateView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        serializer = CreateMatchSerializer(
            data=request.data,
            context={"request": request, "transaction": _get_transaction_or_404(pk)},
        )
        serializer.is_valid(raise_exception=True)
        return Response(BankTransactionMatchSerializer(serializer.save()).data, status=201)


class MatchConfirmView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        match = confirm_match(match_id=pk, organization=request.organization, actor=request.user)
        return Response(BankTransactionMatchSerializer(match).data)


class MatchDeleteView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def delete(self, request, pk):
        transaction = unmatch(match_id=pk, organization=request.organization, actor=request.user)
        return Response(BankTransactionSerializer(transaction).data)


class TransactionCategorizeView(OrganizationScopedMixin, APIView):
    """The one banking endpoint that posts a journal."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        serializer = CategorizeTransactionSerializer(
            data=request.data,
            context={"request": request, "transaction": _get_transaction_or_404(pk)},
        )
        serializer.is_valid(raise_exception=True)
        return Response(BankTransactionMatchSerializer(serializer.save()).data, status=201)


class TransactionUncategorizeView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        transaction = uncategorize_transaction(
            match_id=pk, organization=request.organization, actor=request.user
        )
        return Response(BankTransactionSerializer(transaction).data)


class TransactionApplyRulesView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        outcome = apply_rules_to_transaction(
            transaction_id=pk, organization=request.organization, actor=request.user
        )
        return Response(
            {
                "applied": outcome["applied"],
                "reason": outcome["reason"],
                "rule": BankRuleSerializer(outcome["rule"]).data if outcome["rule"] else None,
            }
        )


# ----------------------------------------------------------- transfers


class TransferCandidatesView(OrganizationScopedMixin, APIView):
    """The opposite leg of a possible unrecorded transfer."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def get(self, request, pk):
        candidates = find_transfer_counterparts(
            transaction=_get_transaction_or_404(pk), organization=request.organization
        )
        return Response(TransferCandidateSerializer(candidates, many=True).data)


class ConfirmTransferPairView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECORD_BANK_TRANSFER

    def post(self, request):
        serializer = ConfirmTransferPairSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transfer = confirm_detected_transfer(
            organization=request.organization, actor=request.user, **serializer.validated_data
        )
        return Response(BankTransferSerializer(transfer).data, status=201)


class BankTransferListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return (
            Permission.VIEW_BANK_TRANSACTIONS
            if self.request.method == "GET"
            else Permission.RECORD_BANK_TRANSFER
        )

    def get_serializer_class(self):
        return BankTransferCreateSerializer if self.request.method == "POST" else BankTransferSerializer

    def get_queryset(self):
        return BankTransfer.objects.all()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(BankTransferSerializer(serializer.save()).data, status=201)


class BankTransferVoidView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECORD_BANK_TRANSFER

    def post(self, request, pk):
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transfer = void_transfer(
            transfer_id=pk,
            organization=request.organization,
            actor=request.user,
            reason=serializer.validated_data["reason"],
        )
        return Response(BankTransferSerializer(transfer).data)


# --------------------------------------------------------------- rules


class BankRuleListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return (
            Permission.VIEW_BANK_TRANSACTIONS
            if self.request.method == "GET"
            else Permission.MANAGE_BANK_RULES
        )

    def get_serializer_class(self):
        return BankRuleCreateSerializer if self.request.method == "POST" else BankRuleSerializer

    def get_queryset(self):
        return BankRule.objects.all()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(BankRuleSerializer(serializer.save()).data, status=201)


class BankRuleDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = BankRuleSerializer

    @property
    def required_permission(self):
        return (
            Permission.VIEW_BANK_TRANSACTIONS
            if self.request.method == "GET"
            else Permission.MANAGE_BANK_RULES
        )

    def get_queryset(self):
        return BankRule.objects.all()

    def update(self, request, *args, **kwargs):
        rule = self.get_object()
        serializer = BankRuleUpdateSerializer(
            data=request.data, partial=True, context={"request": request, "rule": rule}
        )
        serializer.is_valid(raise_exception=True)
        return Response(BankRuleSerializer(serializer.save()).data)


# ------------------------------------------------------ reconciliation


class BankReconciliationListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return (
            Permission.VIEW_BANK_TRANSACTIONS
            if self.request.method == "GET"
            else Permission.RECONCILE_BANK
        )

    def get_serializer_class(self):
        return (
            BankReconciliationCreateSerializer
            if self.request.method == "POST"
            else BankReconciliationSerializer
        )

    def get_queryset(self):
        qs = BankReconciliation.objects.all()
        bank_account = self.request.query_params.get("bank_account")
        if bank_account:
            qs = qs.filter(bank_account_id=bank_account)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(BankReconciliationSerializer(serializer.save()).data, status=201)


class BankReconciliationDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BANK_TRANSACTIONS
    serializer_class = BankReconciliationSerializer

    def get_queryset(self):
        return BankReconciliation.objects.all()


class BankReconciliationSummaryView(OrganizationScopedMixin, APIView):
    """What is blocking closure, before attempting it."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_BANK_TRANSACTIONS

    def get(self, request, pk):
        reconciliation = BankReconciliation.objects.filter(pk=pk).select_related("bank_account").first()
        if reconciliation is None:
            raise ApplicationError(
                "Reconciliation not found.", code="reconciliation_not_found", status_code=404
            )
        summary = get_reconciliation_summary(reconciliation=reconciliation)
        return Response(ReconciliationSummarySerializer(summary).data)


class BankReconciliationCompleteView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        reconciliation = complete_reconciliation(
            reconciliation_id=pk, organization=request.organization, actor=request.user
        )
        return Response(BankReconciliationSerializer(reconciliation).data)


class BankReconciliationReopenView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        serializer = ReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reconciliation = reopen_reconciliation(
            reconciliation_id=pk,
            organization=request.organization,
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(BankReconciliationSerializer(reconciliation).data)


class BankReconciliationAbandonView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.RECONCILE_BANK

    def post(self, request, pk):
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reconciliation = abandon_reconciliation(
            reconciliation_id=pk,
            organization=request.organization,
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(BankReconciliationSerializer(reconciliation).data)
