from rest_framework import serializers

from accounts.models import Currency, Membership, Organization, User
from authz.roles import Role


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

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


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
