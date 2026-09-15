import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from authz.roles import Role
from core.models import TenantScopedModel, TimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """Global identity. Organization membership/role lives on Membership, not
    here — a person can belong to multiple organizations."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class Currency(models.Model):
    """Global reference data (ISO 4217), not organization-owned."""

    code = models.CharField(max_length=3, primary_key=True)
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8)
    decimal_places = models.PositiveSmallIntegerField(default=2)

    class Meta:
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.code


class Organization(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    default_currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="organizations")
    timezone = models.CharField(max_length=64, default="UTC")
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=4)  # 4 = April (India default)
    gstin = models.CharField(max_length=15, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Membership(TimeStampedModel):
    """Links a User to an Organization with a fixed Role. RLS policy allows a
    row to be visible either within the caller's active organization or when
    it belongs to the caller themself (see accounts/migrations for the
    self-or-org RLS policy) — required so a user can discover which
    organizations they belong to before one is selected."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    # Manager scoped by (org OR user) is not the common TenantManager shape;
    # callers use all_objects with an explicit filter (see core/views.py).
    objects = models.Manager()
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="uniq_membership_per_org_user")
        ]

    def __str__(self):
        return f"{self.user_id} @ {self.organization_id} ({self.role})"


class FiscalYear(TenantScopedModel):
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "start_date"], name="uniq_fiscal_year_start_per_org")
        ]
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.organization_id}: {self.start_date} - {self.end_date}"


class NumberSequence(TenantScopedModel):
    """Atomic, gapless-per-request number generation for invoice numbers,
    journal numbers, etc. `key` identifies the sequence, e.g. 'invoice'."""

    key = models.CharField(max_length=64)
    prefix = models.CharField(max_length=16, blank=True)
    next_number = models.PositiveIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=4)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "key"], name="uniq_number_sequence_per_org_key")
        ]

    def __str__(self):
        return f"{self.organization_id}:{self.key}"

    def format(self, number: int) -> str:
        return f"{self.prefix}{str(number).zfill(self.padding)}"
