from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal("0")


def money_field(**kwargs):
    kwargs.setdefault("max_digits", 14)
    kwargs.setdefault("decimal_places", 2)
    kwargs.setdefault("default", ZERO)
    return models.DecimalField(**kwargs)


class InterestType(models.TextChoices):
    FLAT = "flat", "Flat rate"
    REDUCING = "reducing", "Reducing balance"


class Frequency(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    WEEKLY = "weekly", "Weekly"


class AmountType(models.TextChoices):
    PERCENT = "percent", "Percent"
    FIXED = "fixed", "Fixed amount"


class BusinessSettings(models.Model):
    """Single row holding business details, lending defaults and targets."""

    business_name = models.CharField(max_length=120, default="ASK Company")
    logo = models.ImageField(upload_to="logo/", blank=True, null=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=200, blank=True, default="Dar es Salaam, Tanzania")
    currency = models.CharField(max_length=8, default="TZS")
    currency_symbol = models.CharField(max_length=8, default="TSh")

    default_interest_rate = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("10.00"),
                                                help_text="Percent per month")
    default_interest_type = models.CharField(max_length=10, choices=InterestType.choices, default=InterestType.FLAT)
    default_duration_months = models.PositiveIntegerField(default=3)
    default_frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.MONTHLY)

    processing_fee_type = models.CharField(max_length=10, choices=AmountType.choices, default=AmountType.PERCENT)
    processing_fee_value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    penalty_type = models.CharField(max_length=10, choices=AmountType.choices, default=AmountType.PERCENT)
    penalty_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("5.00"),
                                        help_text="Charged once per late installment")
    penalty_grace_days = models.PositiveIntegerField(default=3)
    default_after_days = models.PositiveIntegerField(default=90,
                                                     help_text="Mark a loan defaulted after this many days late")

    monthly_lending_target = money_field()
    monthly_profit_target = money_field()
    yearly_profit_target = money_field()
    yearly_lending_target = money_field()

    reminders_enabled = models.BooleanField(default=False)
    reminder_days_before = models.PositiveIntegerField(default=2)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Business settings"
        verbose_name_plural = "Business settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.business_name


class Borrower(models.Model):
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    national_id = models.CharField("National ID (NIDA)", max_length=40, blank=True)
    address = models.CharField(max_length=200, blank=True)
    photo = models.ImageField(upload_to="borrowers/", blank=True, null=True)
    guarantor_name = models.CharField(max_length=150, blank=True)
    guarantor_phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, help_text="Archived borrowers are hidden, never deleted")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name

    def live_loans(self):
        return self.loans.exclude(status=Loan.Status.VOID)

    @property
    def status(self):
        statuses = set(self.live_loans().filter(balance__gt=0).values_list("status", flat=True))
        if statuses & {Loan.Status.DEFAULTED, Loan.Status.WRITTEN_OFF}:
            return "defaulted"
        if Loan.Status.OVERDUE in statuses:
            return "late"
        return "good"


class Loan(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        OVERDUE = "overdue", "Overdue"
        COMPLETED = "completed", "Completed"
        DEFAULTED = "defaulted", "Defaulted"
        WRITTEN_OFF = "written_off", "Written off"
        VOID = "void", "Void"

    reference = models.CharField(max_length=30, unique=True, editable=False)
    borrower = models.ForeignKey(Borrower, on_delete=models.PROTECT, related_name="loans")
    principal = models.DecimalField(max_digits=14, decimal_places=2)
    interest_rate = models.DecimalField(max_digits=6, decimal_places=2, help_text="Percent per month")
    interest_type = models.CharField(max_length=10, choices=InterestType.choices, default=InterestType.FLAT)
    duration_months = models.PositiveIntegerField()
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.MONTHLY)
    date_issued = models.DateField(default=timezone.localdate)
    first_due_date = models.DateField(null=True, blank=True)

    processing_fee = money_field(help_text="Deducted when the money is given out; counts as income on issue date")
    penalty_type = models.CharField(max_length=10, choices=AmountType.choices, default=AmountType.PERCENT)
    penalty_value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    penalty_grace_days = models.PositiveIntegerField(default=3)

    # Denormalised totals, kept up to date by services.recalculate_loan()
    total_interest = money_field()
    total_penalty = money_field()
    total_due = money_field()
    total_paid = money_field()
    balance = money_field()
    overpayment = money_field()

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    status_locked = models.BooleanField(default=False,
                                        help_text="True when an admin set defaulted/written off by hand")
    void_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    completed_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date_issued", "-id"]

    def __str__(self):
        return f"{self.reference} · {self.borrower}"

    def save(self, *args, **kwargs):
        if not self.reference:
            year = (self.date_issued or timezone.localdate()).year
            prefix = f"AF-{year}-"
            last = (Loan.objects.filter(reference__startswith=prefix)
                    .order_by("-reference").values_list("reference", flat=True).first())
            number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
            self.reference = f"{prefix}{number:04d}"
        super().save(*args, **kwargs)

    @property
    def disbursed_amount(self):
        return self.principal - self.processing_fee

    @property
    def principal_paid(self):
        return self.installments.aggregate(s=Sum("principal_paid"))["s"] or ZERO

    @property
    def interest_paid(self):
        return self.installments.aggregate(s=Sum("interest_paid"))["s"] or ZERO

    @property
    def next_due(self):
        return self.installments.filter(status__in=[Installment.Status.PENDING, Installment.Status.PARTIAL,
                                                    Installment.Status.OVERDUE]).order_by("due_date").first()

    @property
    def days_late(self):
        oldest = self.installments.filter(status=Installment.Status.OVERDUE).order_by("due_date").first()
        if not oldest:
            return 0
        return (timezone.localdate() - oldest.due_date).days


class Installment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PARTIAL = "partial", "Partly paid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="installments")
    number = models.PositiveIntegerField()
    due_date = models.DateField(db_index=True)
    principal_due = money_field()
    interest_due = money_field()
    penalty_due = money_field()
    principal_paid = money_field()
    interest_paid = money_field()
    penalty_paid = money_field()
    penalty_applied_on = models.DateField(null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["loan", "number"]
        unique_together = [("loan", "number")]

    @property
    def amount_due(self):
        return self.principal_due + self.interest_due + self.penalty_due

    @property
    def amount_paid(self):
        return self.principal_paid + self.interest_paid + self.penalty_paid

    @property
    def remaining(self):
        return self.amount_due - self.amount_paid


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        MPESA = "mpesa", "M-Pesa"
        TIGO = "tigo", "Tigo Pesa (Mixx by Yas)"
        AIRTEL = "airtel", "Airtel Money"
        HALOPESA = "halopesa", "HaloPesa"
        BANK = "bank", "Bank"

    receipt_no = models.CharField(max_length=30, unique=True, editable=False)
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField(default=timezone.localdate, db_index=True)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CASH)
    reference = models.CharField("Transaction reference", max_length=80, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    # How the money was split, recalculated whenever the loan's payments change.
    principal_portion = money_field()
    interest_portion = money_field()
    penalty_portion = money_field()
    overpayment_portion = money_field()

    is_void = models.BooleanField(default=False, db_index=True)
    void_reason = models.CharField(max_length=255, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def save(self, *args, **kwargs):
        if not self.receipt_no:
            prefix = f"RC-{(self.date or timezone.localdate()):%Y%m}-"
            last = (Payment.objects.filter(receipt_no__startswith=prefix)
                    .order_by("-receipt_no").values_list("receipt_no", flat=True).first())
            number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
            self.receipt_no = f"{prefix}{number:04d}"
        super().save(*args, **kwargs)

    @property
    def profit_portion(self):
        return self.interest_portion + self.penalty_portion


class ActivityLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="activity")
    action = models.CharField(max_length=40, db_index=True)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    description = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
