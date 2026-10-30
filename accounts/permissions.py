from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    """Only the Django superuser may use the system."""

    message = "Only the system administrator can use ASK Company."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_superuser)
