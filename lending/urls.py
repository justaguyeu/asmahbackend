from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from accounts import views as account_views

from . import views

router = DefaultRouter()
router.register("borrowers", views.BorrowerViewSet, basename="borrower")
router.register("loans", views.LoanViewSet, basename="loan")
router.register("payments", views.PaymentViewSet, basename="payment")
router.register("activity", views.ActivityViewSet, basename="activity")

urlpatterns = [
    path("auth/login/", account_views.LoginView.as_view(), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", account_views.MeView.as_view(), name="me"),
    path("auth/change-password/", account_views.ChangePasswordView.as_view(), name="change-password"),
    path("auth/password-reset/", account_views.PasswordResetRequestView.as_view(), name="password-reset"),
    path("auth/password-reset/confirm/", account_views.PasswordResetConfirmView.as_view(),
         name="password-reset-confirm"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("alerts/", views.AlertsView.as_view(), name="alerts"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("reports/summary/", views.ReportSummaryView.as_view(), name="report-summary"),
    path("reports/compare/", views.ReportCompareView.as_view(), name="report-compare"),
    path("reports/month/", views.ReportMonthView.as_view(), name="report-month"),
    path("reports/borrowers/", views.ReportBorrowersView.as_view(), name="report-borrowers"),
    path("reports/export/", views.ReportExportView.as_view(), name="report-export"),
    path("backup/", views.BackupView.as_view(), name="backup"),
    path("health/", views.HealthView.as_view(), name="health"),
    path("", include(router.urls)),
]
