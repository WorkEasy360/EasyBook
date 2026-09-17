from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from accounts.models import Currency, Membership, Organization, User
from authz.roles import Role

DUPLICATE_EMAIL_MESSAGE = "An account with this email address already exists."


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name"]
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=10)

    class Meta:
        model = User
        fields = ["email", "password", "first_name", "last_name"]
        # The model's UniqueValidator compares case-sensitively; validate_email
        # replaces it so one mailbox cannot hold two accounts.
        extra_kwargs = {"email": {"validators": []}}

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(DUPLICATE_EMAIL_MESSAGE)
        return value

    def validate(self, attrs):
        # AUTH_PASSWORD_VALIDATORS only run when called explicitly;
        # create_user() never calls them. The unsaved user lets the similarity
        # validator compare the password against the email and names.
        candidate = User(
            email=attrs.get("email", ""),
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        try:
            # Savepoint: a concurrent sign-up with the same email hits the
            # unique constraint, which must not poison the request transaction.
            with transaction.atomic():
                return User.objects.create_user(**validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError({"email": [DUPLICATE_EMAIL_MESSAGE]}) from exc


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "is_active", "created_at"]
        read_only_fields = fields


class OrganizationSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            "id", "name", "legal_name", "default_currency", "timezone",
            "fiscal_year_start_month", "gstin", "is_active", "role", "created_at",
        ]
        read_only_fields = ["id", "is_active", "role", "created_at"]

    def get_role(self, obj):
        membership = getattr(obj, "_requesting_membership", None)
        return membership.role if membership else None


class OrganizationCreateSerializer(serializers.ModelSerializer):
    default_currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all())

    class Meta:
        model = Organization
        fields = ["name", "legal_name", "default_currency", "timezone", "fiscal_year_start_month", "gstin"]

    def create(self, validated_data):
        user = self.context["request"].user
        organization = Organization.objects.create(**validated_data)
        Membership.objects.create(organization=organization, user=user, role=Role.OWNER)
        return organization
