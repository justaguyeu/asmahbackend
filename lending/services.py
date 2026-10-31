"""
All the money rules live here so they are tested in one place.

Interest rates are entered as a percent PER MONTH (the way most Tanzanian
lenders quote them, e.g. "10% a month").

* Flat rate:        interest = principal x rate x months, spread equally.
* Reducing balance: equal installments (annuity); each period's interest is
                    charged only on what is still owed.
* Weekly loans:     number of installments = months x 52 / 12 (3 months -> 13
                    weeks); the weekly rate is the monthly rate x 12 / 52.

Payments are applied oldest installment first, and inside each installment in
the order penalty -> interest -> principal. Profit = interest + penalties
actually collected + processing fees (deducted when the money is given out).
"""
import calendar
import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .models import ZERO, AmountType, BusinessSettings, Frequency, Installment, InterestType, Loan, Payment

ONE = Decimal("1")
HUNDRED = Decimal("100")


def to_money(value) -> Decimal:
    """Round to whole shillings (TZS has no coins in everyday use)."""
    return Decimal(value).quantize(ONE, rounding=ROUND_HALF_UP)


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def number_of_installments(duration_months: int, frequency: str) -> int:
    if frequency == Frequency.WEEKLY:
        return max(1, round(duration_months * 52 / 12))
    return max(1, duration_months)


def period_rate(monthly_rate_percent: Decimal, frequency: str) -> Decimal:
    rate = Decimal(monthly_rate_percent) / HUNDRED
    if frequency == Frequency.WEEKLY:
        rate = rate * 12 / 52
    return rate


def due_dates(first_due: date, count: int, frequency: str):
    if frequency == Frequency.WEEKLY:
        return [first_due + timedelta(weeks=i) for i in range(count)]
    return [add_months(first_due, i) for i in range(count)]


def default_first_due(date_issued: date, frequency: str) -> date:
    if frequency == Frequency.WEEKLY:
        return date_issued + timedelta(weeks=1)
    return add_months(date_issued, 1)


def build_schedule(principal, monthly_rate, interest_type, duration_months, frequency, first_due):
    """Return a list of dicts: number, due_date, principal_due, interest_due."""
    principal = to_money(principal)
    count = number_of_installments(duration_months, frequency)
    dates = due_dates(first_due, count, frequency)
    rows = []

    if interest_type == InterestType.REDUCING:
        r = period_rate(monthly_rate, frequency)
        if r == 0:
            payment = principal / count
        else:
            payment = principal * r / (ONE - (ONE + r) ** -count)
        payment = to_money(payment)
        balance = principal
        for i in range(count):
            interest = to_money(balance * r)
            if i == count - 1:
                principal_part = balance
            else:
                principal_part = min(balance, payment - interest)
            balance -= principal_part
            rows.append({"number": i + 1, "due_date": dates[i],
                         "principal_due": principal_part, "interest_due": interest})
    else:
        total_interest = to_money(principal * Decimal(monthly_rate) / HUNDRED * duration_months)
        p_each = to_money(principal / count)
        i_each = to_money(total_interest / count)
        for i in range(count):
            last = i == count - 1
            rows.append({
                "number": i + 1,
                "due_date": dates[i],
                "principal_due": principal - p_each * (count - 1) if last else p_each,
                "interest_due": total_interest - i_each * (count - 1) if last else i_each,
            })
    return rows


def compute_fee(principal, fee_type, fee_value) -> Decimal:
    if not fee_value:
        return ZERO
    if fee_type == AmountType.PERCENT:
        return to_money(Decimal(principal) * Decimal(fee_value) / HUNDRED)
    return to_money(fee_value)


def penalty_amount(loan: Loan, inst: Installment) -> Decimal:
    if not loan.penalty_value:
        return ZERO
    if loan.penalty_type == AmountType.PERCENT:
        return to_money((inst.principal_due + inst.interest_due) * loan.penalty_value / HUNDRED)
    return to_money(loan.penalty_value)


@transaction.atomic
def create_loan(*, borrower, principal, interest_rate=None, interest_type=None, duration_months=None,
                frequency=None, date_issued=None, first_due_date=None, processing_fee=None,
                notes="", user=None) -> Loan:
    s = BusinessSettings.load()
    interest_rate = s.default_interest_rate if interest_rate is None else interest_rate
    interest_type = interest_type or s.default_interest_type
    duration_months = duration_months or s.default_duration_months
    frequency = frequency or s.default_frequency
    date_issued = date_issued or timezone.localdate()
    first_due_date = first_due_date or default_first_due(date_issued, frequency)
    if processing_fee is None:
        processing_fee = compute_fee(principal, s.processing_fee_type, s.processing_fee_value)

    loan = Loan.objects.create(
        borrower=borrower, principal=to_money(principal), interest_rate=interest_rate,
        interest_type=interest_type, duration_months=duration_months, frequency=frequency,
        date_issued=date_issued, first_due_date=first_due_date, processing_fee=to_money(processing_fee),
        penalty_type=s.penalty_type, penalty_value=s.penalty_value,
        penalty_grace_days=s.penalty_grace_days, notes=notes, created_by=user,
    )
    Installment.objects.bulk_create([
        Installment(loan=loan, **row)
        for row in build_schedule(loan.principal, interest_rate, interest_type,
                                  duration_months, frequency, first_due_date)
    ])
    recalculate_loan(loan)
    return loan


@transaction.atomic
def update_loan_terms(loan: Loan, **terms) -> Loan:
    """Correct a loan's terms (amount, interest, duration, dates, fee, penalty). The repayment
    schedule is built again and the payments already received are applied to it again, oldest
    installment first, so balance, profit and status follow the new terms."""
    for field, value in terms.items():
        setattr(loan, field, value)
    loan.principal = to_money(loan.principal)
    loan.processing_fee = to_money(loan.processing_fee)
    if loan.first_due_date is None:
        loan.first_due_date = default_first_due(loan.date_issued, loan.frequency)
    loan.save()
    loan.installments.all().delete()
    Installment.objects.bulk_create([
        Installment(loan=loan, **row)
        for row in build_schedule(loan.principal, loan.interest_rate, loan.interest_type,
                                  loan.duration_months, loan.frequency, loan.first_due_date)
    ])
    return recalculate_loan(loan)


def _allocate(installments, payments, *, with_penalty):
    """Walk payments oldest first and fill installments. Returns per-payment split
    and, per installment, the date principal+interest became fully paid."""
    for inst in installments:
        inst.principal_paid = inst.interest_paid = inst.penalty_paid = ZERO
        inst.paid_date = None
    pi_paid_on = {inst.pk: None for inst in installments}
    splits = {}
    for pay in payments:
        left = pay.amount
        split = {"principal": ZERO, "interest": ZERO, "penalty": ZERO}
        for inst in installments:
            if left <= 0:
                break
            parts = (("penalty", "penalty_due", "penalty_paid"),
                     ("interest", "interest_due", "interest_paid"),
                     ("principal", "principal_due", "principal_paid"))
            for key, due_attr, paid_attr in parts:
                if key == "penalty" and (not with_penalty or inst.penalty_applied_on is None
                                         or inst.penalty_applied_on > pay.date):
                    continue  # a payment can only cover penalties that existed on its date
                owed = getattr(inst, due_attr) - getattr(inst, paid_attr)
                if owed > 0 and left > 0:
                    take = min(owed, left)
                    setattr(inst, paid_attr, getattr(inst, paid_attr) + take)
                    split[key] += take
                    left -= take
            if pi_paid_on[inst.pk] is None and inst.principal_paid >= inst.principal_due \
                    and inst.interest_paid >= inst.interest_due:
                pi_paid_on[inst.pk] = pay.date
            if inst.paid_date is None and inst.remaining <= 0:
                inst.paid_date = pay.date
        split["overpayment"] = max(left, ZERO)
        splits[pay.pk] = split
    return splits, pi_paid_on


@transaction.atomic
def recalculate_loan(loan: Loan, today: date | None = None) -> Loan:
    """Rebuild every paid amount, penalty, payment split, total and status for a
    loan from its schedule and non-void payments. Safe to run any number of times."""
    today = today or timezone.localdate()
    installments = list(loan.installments.order_by("number"))
    payments = list(loan.payments.filter(is_void=False).order_by("date", "id"))

    if loan.status != Loan.Status.VOID:
        # Pass 1: when was each installment's principal+interest paid? -> decide penalties.
        _, pi_paid_on = _allocate(installments, payments, with_penalty=False)
        for inst in installments:
            deadline = inst.due_date + timedelta(days=loan.penalty_grace_days)
            paid_on = pi_paid_on[inst.pk]
            late = today > deadline and (paid_on is None or paid_on > deadline)
            inst.penalty_due = penalty_amount(loan, inst) if late else ZERO
            inst.penalty_applied_on = (deadline + timedelta(days=1)) if late else None

    # Pass 2: real allocation including penalties.
    splits, _ = _allocate(installments, payments, with_penalty=True)

    for inst in installments:
        if inst.remaining <= 0:
            inst.status = Installment.Status.PAID
        elif inst.due_date < today:
            inst.status = Installment.Status.OVERDUE
        elif inst.amount_paid > 0:
            inst.status = Installment.Status.PARTIAL
        else:
            inst.status = Installment.Status.PENDING
    Installment.objects.bulk_update(installments, [
        "principal_paid", "interest_paid", "penalty_paid", "penalty_due",
        "penalty_applied_on", "paid_date", "status"])

    for pay in payments:
        split = splits[pay.pk]
        pay.principal_portion = split["principal"]
        pay.interest_portion = split["interest"]
        pay.penalty_portion = split["penalty"]
        pay.overpayment_portion = split["overpayment"]
    Payment.objects.bulk_update(payments, ["principal_portion", "interest_portion",
                                           "penalty_portion", "overpayment_portion"])
    loan.payments.filter(is_void=True).update(principal_portion=ZERO, interest_portion=ZERO,
                                              penalty_portion=ZERO, overpayment_portion=ZERO)

    loan.total_interest = sum((i.interest_due for i in installments), ZERO)
    loan.total_penalty = sum((i.penalty_due for i in installments), ZERO)
    loan.total_due = loan.principal + loan.total_interest + loan.total_penalty
    loan.total_paid = sum((p.amount for p in payments), ZERO)
    covered = sum((i.amount_paid for i in installments), ZERO)
    loan.balance = max(loan.total_due - covered, ZERO)
    loan.overpayment = sum((s["overpayment"] for s in splits.values()), ZERO)

    if loan.status != Loan.Status.VOID:
        if loan.balance <= 0:
            loan.status = Loan.Status.COMPLETED
            loan.status_locked = False
            loan.completed_on = payments[-1].date if payments else today
        elif loan.status_locked:
            loan.completed_on = None  # admin set defaulted/written off; keep it
        else:
            loan.completed_on = None
            overdue = [i for i in installments if i.status == Installment.Status.OVERDUE]
            if overdue:
                days_late = (today - overdue[0].due_date).days
                limit = BusinessSettings.load().default_after_days
                loan.status = Loan.Status.DEFAULTED if limit and days_late > limit else Loan.Status.OVERDUE
            else:
                loan.status = Loan.Status.ACTIVE
    loan.checked_on = today
    loan.save()
    return loan


# How long one request may spend on the daily refresh before answering; the next requests go on
# where it stopped. Keeps the first page of the day quick even on a slow server.
REFRESH_BUDGET_SECONDS = 2.0


def loans_to_refresh(today: date):
    """Open loans that the passing of days can change and that were not checked today. Only an
    unpaid installment that is already past its due date makes a loan late, adds its (one-off)
    penalty or moves it towards "defaulted"; every other open loan comes out the same."""
    behind = Installment.objects.filter(loan=OuterRef("pk"), due_date__lt=today).exclude(
        status=Installment.Status.PAID)
    return (Loan.objects.exclude(status__in=[Loan.Status.VOID, Loan.Status.COMPLETED])
            .exclude(checked_on=today).filter(Exists(behind)))


def refresh_open_loans(today: date | None = None, force=False) -> int:
    """Bring open loans up to date (statuses and penalties change as days pass). Each loan is
    saved on its own and marked checked_on=today, so the work is shared by all server processes
    and survives a restart; a request spends at most REFRESH_BUDGET_SECONDS on it.
    force=True (manage.py refresh_loans) re-checks every open loan in one go."""
    from django.core.cache import cache

    today = today or timezone.localdate()
    done_key = f"asmah-refreshed-{today.isoformat()}"
    lock_key = "asmah-refresh-running"
    if not force and cache.get(done_key):
        return 0
    if not cache.add(lock_key, True, 120):  # another request is already doing it
        return 0
    try:
        if force:
            loans = Loan.objects.exclude(status__in=[Loan.Status.VOID, Loan.Status.COMPLETED])
        else:
            loans = loans_to_refresh(today)
        stop_at = time.monotonic() + REFRESH_BUDGET_SECONDS
        count = 0
        for loan in list(loans.order_by("id")):
            recalculate_loan(loan, today)
            count += 1
            if not force and time.monotonic() > stop_at:
                return count  # out of time (at least one loan done): the next request carries on
        cache.set(done_key, True, 60 * 60 * 6)
        return count
    finally:
        cache.delete(lock_key)


@transaction.atomic
def record_payment(*, loan: Loan, amount, date_paid=None, method=Payment.Method.CASH,
                   reference="", notes="", user=None) -> Payment:
    payment = Payment.objects.create(
        loan=loan, amount=to_money(amount), date=date_paid or timezone.localdate(),
        method=method, reference=reference, notes=notes, received_by=user,
    )
    recalculate_loan(loan)
    payment.refresh_from_db()
    return payment


@transaction.atomic
def void_payment(payment: Payment, reason: str, user=None) -> Payment:
    payment.is_void = True
    payment.void_reason = reason
    payment.voided_at = timezone.now()
    payment.voided_by = user
    payment.save()
    recalculate_loan(payment.loan)
    return payment


@transaction.atomic
def void_loan(loan: Loan, reason: str, user=None) -> Loan:
    loan.status = Loan.Status.VOID
    loan.void_reason = reason
    loan.save()
    for payment in loan.payments.filter(is_void=False):
        payment.is_void = True
        payment.void_reason = f"Loan voided: {reason}"
        payment.voided_at = timezone.now()
        payment.voided_by = user
        payment.save()
    recalculate_loan(loan)
    return loan


@transaction.atomic
def set_loan_status(loan: Loan, status: str) -> Loan:
    """Admin override: defaulted / written off, or back to automatic tracking."""
    if status in (Loan.Status.DEFAULTED, Loan.Status.WRITTEN_OFF):
        loan.status = status
        loan.status_locked = True
    else:
        loan.status_locked = False
        loan.status = Loan.Status.ACTIVE
    loan.save()
    return recalculate_loan(loan)
