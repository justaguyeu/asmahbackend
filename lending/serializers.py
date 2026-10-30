from decimal import Decimal

from rest_framework import serializers

from .models import ActivityLog, Borrower, BusinessSettings, Installment, Loan, Payment


class RelativeImagesMixin:
    """Return /media/... paths instead of absolute URLs so images work behind the
    Next.js proxy and any domain."""
    image_fields = ()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for name in self.image_fields:
            f = getattr(instance, name, None)
            data[name] = f.url if f else None
        return data


class BusinessSettingsSerializer(RelativeImagesMixin, serializers.ModelSerializer):
    image_fields = ("logo",)

    class Meta:
        model = BusinessSettings
        exclude = ["id"]
        read_only_fields = ["updated_at"]


class BorrowerSerializer(RelativeImagesMixin, serializers.ModelSerializer):
    image_fields = ("photo",)
    status = serializers.CharField(read_only=True)
    balance = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    total_borrowed = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    active_loans = serializers.IntegerField(read_only=True)
    loans_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Borrower
        fields = ["id", "full_name", "phone", "national_id", "address", "photo", "guarantor_name",
                  "guarantor_phone", "notes", "is_active", "created_at", "status", "balance",
                  "total_borrowed", "active_loans", "loans_count"]
        read_only_fields = ["created_at"]

    def validate_full_name(self, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError("Enter the borrower's name.")
        return value


class InstallmentSerializer(serializers.ModelSerializer):
    amount_due = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    amount_paid = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    remaining = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = Installment
        fields = ["id", "number", "due_date", "principal_due", "interest_due", "penalty_due",
                  "principal_paid", "interest_paid", "penalty_paid", "amount_due", "amount_paid",
                  "remaining", "paid_date", "status"]


class PaymentSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="loan.borrower.full_name", read_only=True)
    borrower_id = serializers.IntegerField(source="loan.borrower_id", read_only=True)
    loan_reference = serializers.CharField(source="loan.reference", read_only=True)
    method_display = serializers.CharField(source="get_method_display", read_only=True)
    received_by_name = serializers.CharField(source="received_by.display_name", read_only=True, default=None)
    voided_by_name = serializers.CharField(source="voided_by.display_name", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = ["id", "receipt_no", "loan", "loan_reference", "borrower_name", "borrower_id", "amount",
                  "date", "method", "method_display", "reference", "notes", "principal_portion",
                  "interest_portion", "penalty_portion", "overpayment_portion", "is_void", "void_reason",
                  "voided_at", "voided_by_name", "received_by_name", "created_at"]
        read_only_fields = ["receipt_no", "principal_portion", "interest_portion", "penalty_portion",
                            "overpayment_portion", "is_void", "void_reason", "voided_at", "created_at"]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be more than zero.")
        return value

    def validate_loan(self, loan):
        if loan.status == Loan.Status.VOID:
            raise serializers.ValidationError("This loan has been voided.")
        return loan


class LoanListSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="borrower.full_name", read_only=True)
    borrower_phone = serializers.CharField(source="borrower.phone", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    interest_type_display = serializers.CharField(source="get_interest_type_display", read_only=True)

    class Meta:
        model = Loan
        fields = ["id", "reference", "borrower", "borrower_name", "borrower_phone", "principal",
                  "interest_rate", "interest_type", "interest_type_display", "duration_months", "frequency",
                  "date_issued", "first_due_date", "processing_fee", "total_interest", "total_penalty",
                  "total_due", "total_paid", "balance", "overpayment", "status", "status_display",
                  "completed_on", "created_at"]


class LoanDetailSerializer(LoanListSerializer):
    installments = InstallmentSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    disbursed_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    principal_paid = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    interest_paid = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    days_late = serializers.IntegerField(read_only=True)
    created_by_name = serializers.CharField(source="created_by.display_name", read_only=True, default=None)

    class Meta(LoanListSerializer.Meta):
        fields = LoanListSerializer.Meta.fields + [
            "installments", "payments", "disbursed_amount", "principal_paid", "interest_paid",
            "days_late", "penalty_type", "penalty_value", "penalty_grace_days", "notes", "void_reason",
            "status_locked", "created_by_name"]


class LoanCreateSerializer(serializers.Serializer):
    borrower = serializers.PrimaryKeyRelatedField(queryset=Borrower.objects.filter(is_active=True))
    principal = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("1"))
    interest_rate = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=Decimal("0"),
                                             max_value=Decimal("100"), required=False)
    interest_type = serializers.ChoiceField(choices=["flat", "reducing"], required=False)
    duration_months = serializers.IntegerField(min_value=1, max_value=120, required=False)
    frequency = serializers.ChoiceField(choices=["monthly", "weekly"], required=False)
    date_issued = serializers.DateField(required=False)
    first_due_date = serializers.DateField(required=False, allow_null=True)
    processing_fee = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"),
                                              required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    confirm_existing = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs.get("processing_fee") and attrs["processing_fee"] >= attrs["principal"]:
            raise serializers.ValidationError({"processing_fee": "Fee must be less than the loan amount."})
        first_due, issued = attrs.get("first_due_date"), attrs.get("date_issued")
        if first_due and issued and first_due <= issued:
            raise serializers.ValidationError({"first_due_date": "First due date must be after the issue date."})
        return attrs


class LoanPreviewSerializer(LoanCreateSerializer):
    borrower = serializers.PrimaryKeyRelatedField(queryset=Borrower.objects.all(), required=False)


class BulkLoanSerializer(serializers.Serializer):
    class Item(serializers.Serializer):
        borrower = serializers.PrimaryKeyRelatedField(queryset=Borrower.objects.filter(is_active=True))
        principal = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("1"))

    items = Item(many=True)
    interest_rate = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=Decimal("0"),
                                             max_value=Decimal("100"), required=False)
    interest_type = serializers.ChoiceField(choices=["flat", "reducing"], required=False)
    duration_months = serializers.IntegerField(min_value=1, max_value=120, required=False)
    frequency = serializers.ChoiceField(choices=["monthly", "weekly"], required=False)
    date_issued = serializers.DateField(required=False)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("Choose at least one borrower.")
        ids = [i["borrower"].pk for i in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("A borrower is listed twice.")
        return items


class ActivityLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.display_name", read_only=True, default="System")

    class Meta:
        model = ActivityLog
        fields = ["id", "user", "user_name", "action", "object_type", "object_id", "description", "created_at"]
