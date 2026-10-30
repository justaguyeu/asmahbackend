"""Create the superuser (admin) and ~14 months of realistic sample data.

    python manage.py seed_demo            # adds demo data
    python manage.py seed_demo --reset    # wipes lending data first
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from lending import services
from lending.models import ActivityLog, Borrower, BusinessSettings, Installment, Loan, Payment

FIRST = ["Amina", "Hassan", "Neema", "Juma", "Rehema", "Baraka", "Zawadi", "Salim", "Mwanaidi", "Faraji",
         "Halima", "Omari", "Upendo", "Rashidi", "Saida", "Daudi", "Asha", "Emmanuel", "Grace", "Abdallah",
         "Fatuma", "Joseph", "Mariam", "Khamis", "Esther", "Said", "Winfrida", "Hamisi", "Agnes", "Musa"]
LAST = ["Mussa", "Mwakyusa", "Kimaro", "Massawe", "Mrema", "Shirima", "Mbwambo", "Lyimo", "Njau", "Komba",
        "Mollel", "Swai", "Temba", "Kombo", "Ally", "Hamad", "Mushi", "Kileo", "Mapunda", "Ngowi"]
AREAS = ["Kariakoo", "Sinza", "Mbezi", "Kinondoni", "Temeke", "Ilala", "Tegeta", "Mwenge", "Ubungo",
         "Kimara", "Magomeni", "Buguruni", "Tabata", "Mbagala"]
AMOUNTS = [100_000, 150_000, 200_000, 250_000, 300_000, 400_000, 500_000, 600_000, 800_000, 1_000_000]


class Command(BaseCommand):
    help = "Load demo users and sample lending data"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing lending data first")
        parser.add_argument("--borrowers", type=int, default=36)

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()
        rng = random.Random(42)
        if opts["reset"]:
            Payment.objects.all().delete()
            Installment.objects.all().delete()
            Loan.objects.all().delete()
            Borrower.objects.all().delete()
            ActivityLog.objects.all().delete()

        admin, created = User.objects.get_or_create(
            username="admin", defaults={"first_name": "Asmah", "last_name": "Admin",
                                        "email": "admin@asmahfinance.co.tz", "is_staff": True,
                                        "is_superuser": True, "phone": "0700000001"})
        if created:
            admin.set_password("Asmah@2026")
            admin.save()

        s = BusinessSettings.load()
        s.business_name = "ASK Company"
        s.phone = "+255 700 000 001"
        s.email = "info@asmahfinance.co.tz"
        s.monthly_lending_target = Decimal("8000000")
        s.monthly_profit_target = Decimal("1200000")
        s.yearly_profit_target = Decimal("15000000")
        s.yearly_lending_target = Decimal("90000000")
        s.processing_fee_value = Decimal("2")
        s.save()

        today = timezone.localdate()
        start = (today.replace(day=1) - timedelta(days=430)).replace(day=1)
        borrowers = []
        for i in range(opts["borrowers"]):
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            b = Borrower.objects.create(
                full_name=name, phone=f"07{rng.randint(10, 79)}{rng.randint(100000, 999999)}",
                national_id=f"19{rng.randint(70, 99)}{rng.randint(1000, 1231):04d}-{rng.randint(10000, 99999)}"
                            f"-{rng.randint(10000, 99999)}-{rng.randint(10, 29)}",
                address=f"{rng.choice(AREAS)}, Dar es Salaam",
                guarantor_name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                guarantor_phone=f"07{rng.randint(10, 79)}{rng.randint(100000, 999999)}",
                created_by=admin)
            joined = start + timedelta(days=rng.randint(0, 300))
            Borrower.objects.filter(pk=b.pk).update(created_at=timezone.make_aware(
                timezone.datetime(joined.year, joined.month, joined.day, 9, 0)))
            b.joined = joined
            b.reliability = rng.choices(["good", "ok", "late", "bad"], weights=[66, 22, 9, 3])[0]
            borrowers.append(b)

        loans_made = 0
        month = start
        while month <= today:
            for b in borrowers:
                if b.joined > month or rng.random() > 0.38:
                    continue
                if Loan.objects.filter(borrower=b, status__in=["active", "overdue", "defaulted"], balance__gt=0).exists():
                    continue
                issued = month + timedelta(days=rng.randint(0, 26))
                if issued > today:
                    continue
                loan = services.create_loan(
                    borrower=b, principal=rng.choice(AMOUNTS),
                    interest_rate=Decimal(rng.choice([8, 10, 10, 10, 12, 15])),
                    interest_type=rng.choices(["flat", "reducing"], weights=[75, 25])[0],
                    duration_months=rng.choice([1, 2, 3, 3, 3, 4, 6]),
                    frequency=rng.choices(["monthly", "weekly"], weights=[85, 15])[0],
                    date_issued=issued, user=admin)
                self._pay(loan, b.reliability, rng, today, admin)
                loans_made += 1
            month = services.add_months(month, 1)

        services.refresh_open_loans(force=True)
        ActivityLog.objects.create(user=admin, action="seed", description="Loaded demo data")
        self.stdout.write(self.style.SUCCESS(
            f"Demo ready: {len(borrowers)} borrowers, {loans_made} loans, "
            f"{Payment.objects.count()} payments.\n"
            "  Login: admin / Asmah@2026 (Django superuser)"))

    def _pay(self, loan, reliability, rng, today, user):
        methods = ["mpesa", "mpesa", "mpesa", "cash", "cash", "tigo", "airtel", "bank"]
        stop_after = {"good": 99, "ok": 99, "late": rng.randint(1, 5), "bad": rng.randint(0, 2)}[reliability]
        if reliability == "late" and rng.random() < 0.5:
            stop_after = 99  # most late payers do finish, just slowly
        grace = loan.penalty_grace_days

        def pay(amount, on):
            Payment.objects.create(loan=loan, amount=amount, date=on, method=rng.choice(methods),
                                   reference=f"{rng.choice('QRST')}{rng.randint(10**8, 10**9 - 1)}"
                                   if rng.random() < 0.8 else "", received_by=user)

        for n, inst in enumerate(loan.installments.order_by("number")):
            if n >= stop_after:
                break
            delay = {"good": rng.randint(-3, 1), "ok": rng.randint(-2, 6), "late": rng.randint(2, 15),
                     "bad": rng.randint(10, 30)}[reliability]
            pay_date = max(inst.due_date + timedelta(days=delay), loan.date_issued + timedelta(days=1))
            if pay_date > today:
                break
            amount = inst.principal_due + inst.interest_due
            if delay > grace:
                amount += services.penalty_amount(loan, inst)
            if reliability in ("ok", "late") and rng.random() < 0.2:
                part = services.to_money(amount * Decimal("0.6") / 1000) * 1000
                pay(part, pay_date)
                catch_up = pay_date + timedelta(days=rng.randint(5, 12))
                if catch_up <= today:
                    pay(amount - part, catch_up)
                else:
                    break
            else:
                pay(amount, pay_date)
        services.recalculate_loan(loan, today)
