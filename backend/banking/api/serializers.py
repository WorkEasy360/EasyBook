"""Banking API serializers.

Same two conventions as the other modules: tenant-scoped references are plain
UUID fields resolved at request time (never a class-level queryset, which
would evaluate before tenant context exists), and create-serializers call a
domain service and hand back the instance for a read serializer to render.

One convention specific to this module: `BankAccountSerializer` exposes
`masked_number` and never `account_number_last4` directly, and nothing
anywhere accepts a full account number for storage — see
services/bank_accounts.py.
"""

from decimal import Decimal

from rest_framework import serializers

from accounting.models.account import Account
from accounts.models import Currency
from banking.models.bank_account import BankAccount, BankAccountKind
from banking.models.match import BankTransactionMatch, MatchType
from banking.models.reconciliation import BankReconciliation
from banking.models.rule import BankRule, RuleAction, RuleDirection
from banking.models.statement import BankTransaction, StatementImport
from banking.models.transfer import BankTransfer
from banking.services.bank_accounts import create_bank_account, update_bank_account
from banking.services.imports import import_csv_statement
from banking.services.matching import categorize_transaction, create_match
from banking.services.parsers import AmountMode, CsvColumnMapping
from banking.services.reconciliation import start_reconciliation
from banking.services.rules import create_rule, update_rule
from banking.services.transactions import add_manual_transaction
from banking.services.transfers import record_transfer
from core.exceptions import ApplicationError
from purchases.models.expense import Expense
from purchases.models.payment import VendorPayment
from sales.models.payment import CustomerPayment


def _get_or_404(model, pk, label: str):
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist:
        raise ApplicationError(
            f"{label} not found.", code=f"{label.lower().replace(' ', '_')}_not_found", status_code=404
        )


def _maybe(model, pk, label: str):
    return _get_or_404(model, pk, label) if pk else None


# ------------------------------------------------------- bank accounts


class BankAccountSerializer(serializers.ModelSerializer):
    masked_number = serializers.CharField(read_only=True)

    class Meta:
        model = BankAccount
        fields = [
            "id", "kind", "name", "account", "currency", "bank_name", "masked_number",
            "branch_identifier", "opening_balance", "opening_balance_date", "provider_key",
            "is_active", "notes", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "account", "currency", "created_at", "updated_at"]


class BankAccountCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    account_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=BankAccountKind.choices, required=False, default=BankAccountKind.BANK)
    currency_code = serializers.CharField(max_length=3, required=False, allow_blank=True, default="")
    bank_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    # Write-only and never echoed back: the service keeps only the last four
    # digits, and returning what was submitted would defeat that.
    account_number = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default="", write_only=True
    )
    branch_identifier = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    opening_balance = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, default=Decimal("0")
    )
    opening_balance_date = serializers.DateField(required=False, allow_null=True, default=None)
    provider_key = serializers.CharField(max_length=32, required=False, default="manual")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def create(self, validated_data):
        request = self.context["request"]
        currency_code = validated_data.pop("currency_code", "")
        return create_bank_account(
            organization=request.organization,
            account=_get_or_404(Account, validated_data.pop("account_id"), "Account"),
            currency=_maybe(Currency, currency_code, "Currency"),
            actor=request.user,
            **validated_data,
        )


class BankAccountUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    bank_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    account_number = serializers.CharField(max_length=64, required=False, allow_blank=True, write_only=True)
    branch_identifier = serializers.CharField(max_length=32, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    opening_balance = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    opening_balance_date = serializers.DateField(required=False, allow_null=True)
    provider_key = serializers.CharField(max_length=32, required=False)

    def save(self, **kwargs):
        request = self.context["request"]
        return update_bank_account(
            bank_account=self.context["bank_account"], actor=request.user, **self.validated_data
        )


class BankAccountSummarySerializer(serializers.Serializer):
    as_of = serializers.DateField(allow_null=True)
    book_balance = serializers.DecimalField(max_digits=18, decimal_places=2)
    statement_balance = serializers.DecimalField(max_digits=18, decimal_places=2)
    cleared_balance = serializers.DecimalField(max_digits=18, decimal_places=2)
    unexplained_statement_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    uncleared_book_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    open_transaction_count = serializers.IntegerField()


# ---------------------------------------------------------- statements


class StatementImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatementImport
        fields = [
            "id", "bank_account", "source_format", "file_name", "status",
            "statement_start_date", "statement_end_date", "rows_read", "rows_imported",
            "rows_skipped_duplicate", "error_message", "created_at",
        ]
        read_only_fields = fields


class CsvColumnMappingSerializer(serializers.Serializer):
    """The mapping is required, never inferred — see services/parsers.py."""

    date_column = serializers.CharField(max_length=128)
    amount_mode = serializers.ChoiceField(
        choices=[AmountMode.SIGNED, AmountMode.DEBIT_CREDIT, AmountMode.INDICATOR],
        required=False, default=AmountMode.SIGNED,
    )
    amount_column = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    debit_column = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    credit_column = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    indicator_column = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    description_column = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    counterparty_column = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    reference_column = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    external_id_column = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    date_formats = serializers.ListField(
        child=serializers.CharField(max_length=32), required=False, allow_empty=True, default=list
    )
    invert_sign = serializers.BooleanField(required=False, default=False)

    def to_mapping(self) -> CsvColumnMapping:
        data = dict(self.validated_data)
        formats = data.pop("date_formats", None)
        mapping = CsvColumnMapping(**data)
        if formats:
            mapping.date_formats = tuple(formats)
        return mapping


class StatementImportCreateSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False)
    file_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    mapping = CsvColumnMappingSerializer()

    def create(self, validated_data):
        request = self.context["request"]
        mapping_serializer = CsvColumnMappingSerializer(data=self.initial_data["mapping"])
        mapping_serializer.is_valid(raise_exception=True)
        return import_csv_statement(
            organization=request.organization,
            bank_account=self.context["bank_account"],
            content=validated_data["content"],
            mapping=mapping_serializer.to_mapping(),
            file_name=validated_data["file_name"],
            actor=request.user,
        )


class BankTransactionSerializer(serializers.ModelSerializer):
    is_inflow = serializers.BooleanField(read_only=True)

    class Meta:
        model = BankTransaction
        fields = [
            "id", "bank_account", "statement_import", "transaction_date", "amount",
            "is_inflow", "description", "counterparty_name", "bank_reference",
            "external_id", "status", "excluded_reason", "reconciliation",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class BankTransactionCreateSerializer(serializers.Serializer):
    transaction_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    counterparty_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    bank_reference = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    external_id = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def create(self, validated_data):
        request = self.context["request"]
        return add_manual_transaction(
            organization=request.organization,
            bank_account=self.context["bank_account"],
            actor=request.user,
            **validated_data,
        )


class ExcludeTransactionSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


# ------------------------------------------------------------- matches


class BankTransactionMatchSerializer(serializers.ModelSerializer):
    counterpart_type = serializers.CharField(read_only=True)

    class Meta:
        model = BankTransactionMatch
        fields = [
            "id", "transaction", "counterpart_type", "customer_payment", "vendor_payment",
            "expense", "bank_transfer", "journal_entry", "amount", "match_type",
            "suggestion_source", "confidence", "reason", "is_confirmed", "confirmed_at",
            "created_at",
        ]
        read_only_fields = fields


_COUNTERPART_MODELS = {
    "customer_payment": (CustomerPayment, "Customer payment"),
    "vendor_payment": (VendorPayment, "Vendor payment"),
    "expense": (Expense, "Expense"),
    "bank_transfer": (BankTransfer, "Transfer"),
}


class CreateMatchSerializer(serializers.Serializer):
    counterpart_type = serializers.ChoiceField(choices=sorted(_COUNTERPART_MODELS))
    counterpart_id = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )

    def create(self, validated_data):
        request = self.context["request"]
        field = validated_data["counterpart_type"]
        model, label = _COUNTERPART_MODELS[field]
        return create_match(
            transaction_id=self.context["transaction"].id,
            organization=request.organization,
            counterpart_field=field,
            counterpart=_get_or_404(model, validated_data["counterpart_id"], label),
            amount=validated_data["amount"],
            match_type=MatchType.MANUAL,
            actor=request.user,
            confirm=True,
        )


class CategorizeTransactionSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    description = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def create(self, validated_data):
        request = self.context["request"]
        return categorize_transaction(
            transaction_id=self.context["transaction"].id,
            organization=request.organization,
            account=_get_or_404(Account, validated_data["account_id"], "Account"),
            description=validated_data["description"],
            actor=request.user,
        )


class TransferCandidateSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    bank_account = serializers.UUIDField(source="bank_account_id")
    bank_account_name = serializers.CharField(source="bank_account.name")
    transaction_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    description = serializers.CharField()


class ConfirmTransferPairSerializer(serializers.Serializer):
    outflow_transaction_id = serializers.UUIDField()
    inflow_transaction_id = serializers.UUIDField()
    reference = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


# ----------------------------------------------------------- transfers


class BankTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankTransfer
        fields = [
            "id", "transfer_number", "from_bank_account", "to_bank_account", "transfer_date",
            "amount", "currency", "reference", "description", "status", "accounting_journal",
            "voided_at", "void_reason", "created_at",
        ]
        read_only_fields = fields


class BankTransferCreateSerializer(serializers.Serializer):
    from_bank_account_id = serializers.UUIDField()
    to_bank_account_id = serializers.UUIDField()
    transfer_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    reference = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")

    def create(self, validated_data):
        request = self.context["request"]
        return record_transfer(
            organization=request.organization,
            from_bank_account=_get_or_404(
                BankAccount, validated_data.pop("from_bank_account_id"), "Bank account"
            ),
            to_bank_account=_get_or_404(
                BankAccount, validated_data.pop("to_bank_account_id"), "Bank account"
            ),
            actor=request.user,
            **validated_data,
        )


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


# --------------------------------------------------------------- rules


class BankRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankRule
        fields = [
            "id", "name", "priority", "is_active", "bank_account", "description_contains",
            "counterparty_contains", "direction", "amount_min", "amount_max", "action",
            "target_account", "vendor", "customer", "auto_confirm", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class BankRuleCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    action = serializers.ChoiceField(choices=RuleAction.choices, required=False, default=RuleAction.CATEGORIZE)
    target_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    bank_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    description_contains = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    counterparty_contains = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    direction = serializers.ChoiceField(choices=RuleDirection.choices, required=False, default=RuleDirection.ANY)
    amount_min = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    amount_max = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    auto_confirm = serializers.BooleanField(required=False, default=False)
    priority = serializers.IntegerField(required=False, default=100, min_value=0)

    def create(self, validated_data):
        request = self.context["request"]
        return create_rule(
            organization=request.organization,
            target_account=_maybe(Account, validated_data.pop("target_account_id"), "Account"),
            bank_account=_maybe(BankAccount, validated_data.pop("bank_account_id"), "Bank account"),
            actor=request.user,
            **validated_data,
        )


class BankRuleUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    priority = serializers.IntegerField(required=False, min_value=0)
    is_active = serializers.BooleanField(required=False)
    description_contains = serializers.CharField(max_length=255, required=False, allow_blank=True)
    counterparty_contains = serializers.CharField(max_length=255, required=False, allow_blank=True)
    direction = serializers.ChoiceField(choices=RuleDirection.choices, required=False)
    amount_min = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    amount_max = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    action = serializers.ChoiceField(choices=RuleAction.choices, required=False)
    target_account_id = serializers.UUIDField(required=False, allow_null=True)
    auto_confirm = serializers.BooleanField(required=False)

    def save(self, **kwargs):
        request = self.context["request"]
        fields = dict(self.validated_data)
        if "target_account_id" in fields:
            fields["target_account"] = _maybe(Account, fields.pop("target_account_id"), "Account")
        return update_rule(rule=self.context["rule"], actor=request.user, **fields)


# ------------------------------------------------------ reconciliation


class BankReconciliationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankReconciliation
        fields = [
            "id", "bank_account", "statement_start_date", "statement_end_date",
            "statement_closing_balance", "status", "cleared_balance", "notes",
            "completed_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "status", "cleared_balance", "completed_at", "created_at", "updated_at"]


class BankReconciliationCreateSerializer(serializers.Serializer):
    bank_account_id = serializers.UUIDField()
    statement_start_date = serializers.DateField()
    statement_end_date = serializers.DateField()
    statement_closing_balance = serializers.DecimalField(max_digits=18, decimal_places=2)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def create(self, validated_data):
        request = self.context["request"]
        return start_reconciliation(
            organization=request.organization,
            bank_account=_get_or_404(
                BankAccount, validated_data.pop("bank_account_id"), "Bank account"
            ),
            actor=request.user,
            **validated_data,
        )


class ReconciliationSummarySerializer(serializers.Serializer):
    statement_closing_balance = serializers.DecimalField(max_digits=18, decimal_places=2)
    cleared_balance = serializers.DecimalField(max_digits=18, decimal_places=2)
    difference = serializers.DecimalField(max_digits=18, decimal_places=2)
    book_balance = serializers.DecimalField(max_digits=18, decimal_places=2)
    transaction_count = serializers.IntegerField()
    open_transaction_count = serializers.IntegerField()
    is_balanced = serializers.BooleanField()
    can_complete = serializers.BooleanField()


class ReopenSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
