from django.contrib import admin

from .models import ActivityLog, Borrower, BusinessSettings, Installment, Loan, Payment


class InstallmentInline(admin.TabularInline):
    model = Installment
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in Installment._meta.fields if f.name != "id"]


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("reference", "borrower", "principal", "interest_rate", "date_issued", "balance", "status")
    list_filter = ("status", "interest_type", "frequency")
    search_fields = ("reference", "borrower__full_name", "borrower__phone")
    readonly_fields = ("reference", "total_interest", "total_penalty", "total_due", "total_paid", "balance")
    inlines = [InstallmentInline]

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("receipt_no", "loan", "amount", "date", "method", "is_void")
    list_filter = ("method", "is_void")
    search_fields = ("receipt_no", "reference", "loan__borrower__full_name")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Borrower)
class BorrowerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "national_id", "is_active", "created_at")
    search_fields = ("full_name", "phone", "national_id")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "description")
    list_filter = ("action",)
    readonly_fields = [f.name for f in ActivityLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(BusinessSettings)
