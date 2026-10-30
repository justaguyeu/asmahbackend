from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import reports, services
from .models import Borrower, BusinessSettings, Installment, Loan, Payment

D = Decimal


class ScheduleTests(TestCase):
    def test_flat_rate_three_months(self):
        rows = services.build_schedule(D("100000"), D("10"), "flat", 3, "monthly", date(2026, 2, 1))
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(r["interest_due"] for r in rows), D("30000"))
        self.assertEqual(sum(r["principal_due"] for r in rows), D("100000"))
        self.assertEqual([r["due_date"] for r in rows], [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)])

    def test_flat_rounding_goes_to_last_installment(self):
        rows = services.build_schedule(D("100000"), D("10"), "flat", 7, "monthly", date(2026, 1, 31))
        self.assertEqual(sum(r["principal_due"] for r in rows), D("100000"))
        self.assertEqual(sum(r["interest_due"] for r in rows), D("70000"))
        self.assertEqual(rows[1]["due_date"], date(2026, 2, 28))  # month-end clamp

    def test_reducing_balance_charges_less_interest_than_flat(self):
        flat = services.build_schedule(D("100000"), D("10"), "flat", 3, "monthly", date(2026, 2, 1))
        red = services.build_schedule(D("100000"), D("10"), "reducing", 3, "monthly", date(2026, 2, 1))
        self.assertEqual(sum(r["principal_due"] for r in red), D("100000"))
        self.assertEqual(red[0]["interest_due"], D("10000"))  # first month: 10% of full balance
        self.assertLess(sum(r["interest_due"] for r in red), sum(r["interest_due"] for r in flat))
        payments = [r["principal_due"] + r["interest_due"] for r in red]
        self.assertLessEqual(max(payments) - min(payments), D("2"))  # equal installments

    def test_weekly(self):
        rows = services.build_schedule(D("130000"), D("10"), "flat", 3, "weekly", date(2026, 1, 8))
        self.assertEqual(len(rows), 13)
        self.assertEqual(rows[1]["due_date"], date(2026, 1, 15))
        self.assertEqual(sum(r["interest_due"] for r in rows), D("39000"))


class LoanLifecycleTests(TestCase):
    def setUp(self):
        s = BusinessSettings.load()
        s.penalty_type, s.penalty_value, s.penalty_grace_days = "percent", D("5"), 3
        s.processing_fee_value = D("0")
        s.save()
        self.b = Borrower.objects.create(full_name="Amina Juma", phone="0712000000")
        self.issued = date.today() - timedelta(days=100)
        self.loan = services.create_loan(borrower=self.b, principal=D("300000"), interest_rate=D("10"),
                                         interest_type="flat", duration_months=3, frequency="monthly",
                                         date_issued=self.issued)

    def pay(self, amount, on):
        return services.record_payment(loan=self.loan, amount=D(amount), date_paid=on)

    def test_overdue_with_penalties_when_unpaid(self):
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.OVERDUE)
        # 3 installments of 130,000 all late -> 5% penalty each = 6,500 x 3
        self.assertEqual(self.loan.total_penalty, D("19500"))
        self.assertEqual(self.loan.balance, D("390000") + D("19500"))

    def test_on_time_payments_complete_loan_and_split_profit(self):
        for inst in self.loan.installments.order_by("number"):
            self.pay("130000", inst.due_date)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.COMPLETED)
        self.assertEqual(self.loan.balance, 0)
        self.assertEqual(self.loan.total_penalty, 0)
        pays = Payment.objects.filter(loan=self.loan)
        self.assertEqual(sum(p.interest_portion for p in pays), D("90000"))
        self.assertEqual(sum(p.principal_portion for p in pays), D("300000"))

    def test_late_payment_pays_penalty_first(self):
        first = self.loan.installments.get(number=1)
        p = self.pay("136500", first.due_date + timedelta(days=10))
        self.assertEqual(p.penalty_portion, D("6500"))
        self.assertEqual(p.interest_portion, D("30000"))
        self.assertEqual(p.principal_portion, D("100000"))
        first.refresh_from_db()
        self.assertEqual(first.status, Installment.Status.PAID)

    def test_partial_and_overpayment(self):
        p1 = self.pay("50000", self.issued + timedelta(days=5))
        self.assertEqual(p1.interest_portion, D("30000"))
        self.assertEqual(p1.principal_portion, D("20000"))
        p2 = self.pay("1000000", date.today())
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.COMPLETED)
        self.assertGreater(p2.overpayment_portion, 0)
        self.assertEqual(self.loan.overpayment, p2.overpayment_portion)

    def test_void_payment_restores_balance(self):
        before = self.loan.balance
        p = self.pay("130000", self.issued + timedelta(days=20))
        self.loan.refresh_from_db()
        self.assertLess(self.loan.balance, before)
        services.void_payment(p, "Wrong borrower")
        self.loan.refresh_from_db()
        p.refresh_from_db()
        self.assertEqual(self.loan.balance, before)
        self.assertEqual(p.interest_portion, 0)
        self.assertTrue(Payment.objects.filter(pk=p.pk).exists())  # never deleted

    def test_defaulted_override_and_back(self):
        services.set_loan_status(self.loan, "written_off")
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.WRITTEN_OFF)
        services.recalculate_loan(self.loan)
        self.assertEqual(self.loan.status, Loan.Status.WRITTEN_OFF)
        services.set_loan_status(self.loan, "auto")
        self.assertEqual(self.loan.status, Loan.Status.OVERDUE)


class ReportTests(TestCase):
    def test_profit_counts_only_money_received(self):
        b = Borrower.objects.create(full_name="Juma Hamisi")
        s = BusinessSettings.load()
        s.processing_fee_type, s.processing_fee_value = "fixed", D("5000")
        s.save()
        loan = services.create_loan(borrower=b, principal=D("200000"), interest_rate=D("10"),
                                    interest_type="flat", duration_months=2, frequency="monthly",
                                    date_issued=date(2026, 1, 10))
        services.record_payment(loan=loan, amount=D("120000"), date_paid=date(2026, 2, 10))
        jan = reports.period_summary(date(2026, 1, 1), date(2026, 1, 31))
        feb = reports.period_summary(date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(jan["lent"], D("200000"))
        self.assertEqual(jan["profit"], D("5000"))  # only the fee; no interest received yet
        self.assertEqual(feb["interest_collected"], D("20000"))
        self.assertEqual(feb["profit"], D("20000"))
        self.assertEqual(feb["collection_rate"], D("100.0"))
        rows, totals = reports.monthly_rows(date(2026, 1, 1), date(2026, 3, 31))
        self.assertEqual(len(rows), 3)
        self.assertEqual(totals["profit"], D("25000"))


class ApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("boss", "boss@x.co", "Pass-word-123")
        self.normal = User.objects.create_user("clerk", "clerk@x.co", "Pass-word-123")
        self.client = APIClient()

    def login(self, username):
        r = self.client.post("/api/auth/login/", {"username": username, "password": "Pass-word-123"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        return r

    def test_login_by_email_and_requires_auth(self):
        self.assertEqual(self.client.get("/api/dashboard/").status_code, 401)
        r = self.client.post("/api/auth/login/", {"username": "boss@x.co", "password": "Pass-word-123"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["user"]["username"], "boss")

    def test_full_flow(self):
        self.login("boss")
        r = self.client.post("/api/borrowers/bulk/", {"text": "Neema Kimaro, 0711111111\nBaraka Mrema, 0722222222"},
                             format="json")
        self.assertEqual(r.data["created"], 2)
        b = Borrower.objects.get(full_name="Neema Kimaro")
        r = self.client.post("/api/loans/preview/", {"principal": "500000", "interest_rate": "10",
                                                     "duration_months": 2}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.client.post("/api/loans/", {"borrower": b.id, "principal": "500000", "interest_rate": "10",
                                             "duration_months": 2, "interest_type": "flat"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        loan_id = r.data["id"]
        # second loan warns first
        r = self.client.post("/api/loans/", {"borrower": b.id, "principal": "100000"}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["code"], "existing_loan")
        r = self.client.post("/api/payments/", {"loan": loan_id, "amount": "300000", "method": "mpesa",
                                                "reference": "QX12345"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.data["id"]
        self.assertEqual(self.client.get(f"/api/payments/{pid}/receipt/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/borrowers/{b.id}/statement/").status_code, 200)
        r = self.client.get("/api/dashboard/?range=12m")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["chart"]), 12)
        r = self.client.get("/api/reports/summary/?start=2025-01-01&end=2026-12-31")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["months"]), 24)
        for fmt in ("xlsx", "pdf"):
            self.assertEqual(self.client.get(f"/api/reports/export/?type={fmt}").status_code, 200)
        r = self.client.post(f"/api/payments/{pid}/void/", {"reason": "Entered twice"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.client.get("/api/activity/").data["count"] >= 5)
        self.assertEqual(self.client.get("/api/backup/").status_code, 200)

    def test_monthly_round(self):
        self.login("boss")
        a = Borrower.objects.create(full_name="Asha Ally")
        c = Borrower.objects.create(full_name="Daudi Swai")
        r = self.client.post("/api/loans/bulk/", {"items": [{"borrower": a.id, "principal": "200000"},
                                                            {"borrower": c.id, "principal": "300000"}],
                                                  "interest_rate": "10", "duration_months": 1}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["created"], 2)
        self.assertEqual(D(str(r.data["total"])), D("500000"))

    def test_only_superuser_can_use_system(self):
        r = self.client.post("/api/auth/login/", {"username": "clerk", "password": "Pass-word-123"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("administrator", str(r.data))
        # even with a valid token, a non-superuser gets nothing
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.normal).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        for url in ("/api/dashboard/", "/api/borrowers/", "/api/loans/", "/api/reports/summary/", "/api/auth/me/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post("/api/auth/password-reset/", {"email": "clerk@x.co"}, format="json").status_code, 200)
        from django.core import mail
        self.assertEqual(len(mail.outbox), 0)  # no reset link for non-admins

    def test_no_hard_delete(self):
        self.login("boss")
        b = Borrower.objects.create(full_name="Saida Komba")
        loan = services.create_loan(borrower=b, principal=D("100000"), interest_rate=D("10"), duration_months=1)
        self.assertEqual(self.client.delete(f"/api/loans/{loan.id}/").status_code, 405)
        self.assertEqual(self.client.delete(f"/api/borrowers/{b.id}/").status_code, 405)

    def test_password_reset_flow(self):
        from django.core import mail
        r = self.client.post("/api/auth/password-reset/", {"email": "boss@x.co"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        link = [l for l in mail.outbox[0].body.split() if "reset-password" in l][0]
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(link).query)
        r = self.client.post("/api/auth/password-reset/confirm/", {"uid": q["uid"][0], "token": q["token"][0],
                                                                   "new_password": "Brand-new-pass-9"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.client.post("/api/auth/login/", {"username": "boss", "password": "Brand-new-pass-9"}, format="json")
        self.assertEqual(r.status_code, 200)
