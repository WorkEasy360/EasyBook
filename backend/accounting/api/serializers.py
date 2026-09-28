from decimal import Decimal

from rest_framework import serializers

from accounting.models.account import Account
from accounting.models.journal import JournalEntry, JournalLine
from accounting.services.accounts import create_account, update_account
from accounting.services.journals import create_draft_journal, replace_draft_lines
from accounting.services.posting import reverse_journal
from accounts.models import Currency, FiscalYear


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = [
            "id", "code", "name", "account_type", "account_subtype", "parent",
            "is_active", "is_system", "description", "created_at", "updated_at",
        ]
        # is_system is platform-assigned, never client-settable through the API.
        read_only_fields = ["id", "is_system", "created_at", "updated_at"]

    def validate_code(self, value):
        # The (organization, code) UniqueConstraint is the backstop, but hitting
        # it surfaces as an IntegrityError -> HTTP 500 for an ordinary typo.
        # Checked here so the client gets a 400 on the `code` field instead.
        request = self.context["request"]
        existing = Account.objects.filter(organization=request.organization, code=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(f"An account with code '{value}' already exists.")
        return value

    def create(self, validated_data):
        request = self.context["request"]
        return create_account(organization=request.organization, actor=request.user, **validated_data)

    def update(self, instance, validated_data):
        request = self.context["request"]
        return update_account(account=instance, actor=request.user, **validated_data)


class JournalLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = JournalLine
        fields = ["id", "account", "line_number", "description", "debit", "credit"]
        read_only_fields = fields


class JournalLineInputSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    description = serializers.CharField(required=False, allow_blank=True, default="")
    debit = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0"))
    credit = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0"))


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True, read_only=True)

    class Meta:
        model = JournalEntry
        fields = [
            "id", "journal_number", "reference", "posting_date", "memo", "status",
            "source_type", "source_id", "currency", "exchange_rate", "fiscal_year",
            "created_by", "posted_by", "posted_at", "reverses", "lines",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "journal_number", "status", "fiscal_year", "created_by", "posted_by",
            "posted_at", "reverses", "lines", "created_at", "updated_at",
        ]


class JournalEntryCreateSerializer(serializers.Serializer):
    posting_date = serializers.DateField()
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all())
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    memo = serializers.CharField(required=False, allow_blank=True, default="")
    source_type = serializers.CharField(required=False, allow_blank=True, default="")
    source_id = serializers.CharField(required=False, allow_blank=True, default="")
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, default=Decimal("1"))
    lines = JournalLineInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context["request"]
        lines = validated_data.pop("lines")
        return create_draft_journal(
            organization=request.organization, created_by=request.user, lines=lines, **validated_data
        )


class JournalEntryLinesUpdateSerializer(serializers.Serializer):
    lines = JournalLineInputSerializer(many=True)

    def save(self, **kwargs):
        return replace_draft_lines(journal=self.context["journal"], lines=self.validated_data["lines"])


class JournalEntryReverseSerializer(serializers.Serializer):
    posting_date = serializers.DateField(required=False)
    memo = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, **kwargs):
        request = self.context["request"]
        journal = self.context["journal"]
        return reverse_journal(
            journal_id=journal.id,
            organization=request.organization,
            actor=request.user,
            posting_date=self.validated_data.get("posting_date"),
            memo=self.validated_data.get("memo", ""),
        )


class FiscalYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalYear
        fields = ["id", "start_date", "end_date", "is_closed", "created_at"]
        read_only_fields = fields


class FiscalYearCreateSerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date = serializers.DateField()
