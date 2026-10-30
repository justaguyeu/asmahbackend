from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class AsmahUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Asmah Finance", {"fields": ("phone",)}),)
    list_display = ("username", "email", "first_name", "last_name", "is_superuser", "is_active")
