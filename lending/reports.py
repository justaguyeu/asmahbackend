"""
Reporting. Every profit figure comes from money actually received:
interest + penalties collected in payments, plus processing fees on loans
issued in the period (fees are deducted when the money is handed out).
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import ZERO, Borrower, BusinessSettings, Installment, Loan, Payment

DEC = DecimalField(max_digits=16, decimal_places=2)
OPEN_STATUSES = [Loan.Status.ACTIVE, Loan.Status.OVERDUE, Loan.Status.DEFAULTED, Loan.Status.WRITTEN_OFF]
LOST_STATUSES = [Loan.Status.DEFAULTED, Loan.Status.WRITTEN_OFF]


def _sum(qs, expr):
    return qs.aggregate(v=Coalesce(Sum(expr, output_field=DEC), ZERO, output_field=DEC))["v"]


def live_loans():
    return Loan.objects.exclude(status=Loan.Status.VOID)


def live_payments():
    return Payment.objects.filter(is_void=False).exclude(loan__status=Loan.Status.VOID)


def live_installments():
    return Installment.objects.exclude(loan__status=Loan.Status.VOID)


REMAINING = ExpressionWrapper(
    F("principal_due") + F("interest_due") + F("penalty_due")
    - F("principal_paid") - F("interest_paid") - F("penalty_paid"), output_field=DEC)


def month_bounds(year: int, month: int):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def iter_months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def pct_change(current, previous):
    current, previous = Decimal(current or 0), Decimal(previous or 0)
    if previous == 0:
        return None if current == 0 else Decimal("100")
    return ((current - previous) / abs(previous) * 100).quantize(Decimal("0.1"))


def period_summary(start: date, end: date) -> dict:
    loans = live_loans().filter(date_issued__range=(start, end))
    pays = live_payments().filter(date__range=(start, end))
    insts = live_installments().filter(due_date__range=(start, end))

    lent = _sum(loans, "principal")
    fees = _sum(loans, "processing_fee")
    interest = _sum(pays, "interest_portion")
    penalties = _sum(pays, "penalty_portion")
    principal_back = _sum(pays, "principal_portion")
    collected = _sum(pays, "amount")
    expected_interest = _sum(insts, "interest_due")
    due_pi = _sum(insts, F("principal_due") + F("interest_due"))
    paid_pi = _sum(insts, F("principal_paid") + F("interest_paid"))
    overdue_amount = _sum(insts.filter(status=Installment.Status.OVERDUE), REMAINING)

    return {
        "start": start, "end": end,
        "lent": lent,
        "loans_issued": loans.count(),
        "collected": collected,
        "principal_collected": principal_back,
        "interest_collected": interest,
        "penalties_collected": penalties,
        "fees_collected": fees,
        "fees_and_penalties": fees + penalties,
        "profit": interest + penalties + fees,
        "expected_interest": expected_interest,
        "expected_profit": expected_interest + fees,
        "outstanding": _sum(loans.filter(status__in=OPEN_STATUSES), "balance"),
        "overdue_amount": overdue_amount,
        "lost_amount": _sum(loans.filter(status__in=LOST_STATUSES), "balance"),
        "due_amount": due_pi,
        "collection_rate": (paid_pi / due_pi * 100).quantize(Decimal("0.1")) if due_pi else None,
        "new_borrowers": Borrower.objects.filter(created_at__date__range=(start, end)).count(),
        "borrowers_served": loans.values("borrower").distinct().count(),
        "payments_count": pays.count(),
    }


ROW_KEYS = ["lent", "collected", "interest_collected", "fees_and_penalties", "profit",
            "outstanding", "overdue_amount", "loans_issued", "collection_rate"]


def monthly_rows(start: date, end: date):
    rows = []
    for y, m in iter_months(start, end):
        m_start, m_end = month_bounds(y, m)
        s = period_summary(max(m_start, start), min(m_end, end))
        rows.append({"year": y, "month": m, "label": f"{calendar.month_abbr[m]} {y}",
                     **{k: s[k] for k in ROW_KEYS}})
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ROW_KEYS if k != "collection_rate"}
    return rows, totals


def yearly_rows():
    first = live_loans().order_by("date_issued").values_list("date_issued", flat=True).first()
    today = timezone.localdate()
    if not first:
        return []
    rows, previous = [], None
    for y in range(first.year, today.year + 1):
        s = period_summary(date(y, 1, 1), date(y, 12, 31))
        row = {"year": y, **{k: s[k] for k in ROW_KEYS}}
        row["profit_growth"] = pct_change(row["profit"], previous["profit"]) if previous else None
        row["lent_growth"] = pct_change(row["lent"], previous["lent"]) if previous else None
        rows.append(row)
        previous = row
    return rows


def month_detail(year: int, month: int) -> dict:
    start, end = month_bounds(year, month)
    loans = live_loans().filter(date_issued__range=(start, end)).select_related("borrower")
    pays = live_payments().filter(date__range=(start, end)).select_related("loan__borrower")
    owing = (live_installments().filter(due_date__range=(start, end))
             .exclude(status=Installment.Status.PAID).select_related("loan__borrower")
             .annotate(left=REMAINING).order_by("due_date"))
    return {
        "summary": period_summary(start, end),
        "loans": [{"id": l.id, "reference": l.reference, "borrower": l.borrower.full_name,
                   "borrower_id": l.borrower_id, "principal": l.principal, "date_issued": l.date_issued,
                   "total_interest": l.total_interest, "balance": l.balance, "status": l.status} for l in loans],
        "payments": [{"id": p.id, "receipt_no": p.receipt_no, "borrower": p.loan.borrower.full_name,
                      "loan_reference": p.loan.reference, "amount": p.amount, "date": p.date,
                      "method": p.get_method_display(), "method_code": p.method, "interest": p.interest_portion,
                      "principal": p.principal_portion} for p in pays],
        "still_owing": [{"loan_id": i.loan_id, "loan_reference": i.loan.reference,
                         "borrower": i.loan.borrower.full_name, "borrower_id": i.loan.borrower_id,
                         "phone": i.loan.borrower.phone, "due_date": i.due_date,
                         "amount_left": i.left, "status": i.status} for i in owing],
    }


def borrowers_in_period(start: date, end: date):
    """Borrowers who had a loan running at any point in the period."""
    active_loans = live_loans().filter(date_issued__lte=end).filter(
        Q(completed_on__isnull=True) | Q(completed_on__gte=start))
    ids = active_loans.values_list("borrower_id", flat=True).distinct()
    rows = []
    for b in Borrower.objects.filter(id__in=ids):
        bl = live_loans().filter(borrower=b)
        rows.append({
            "id": b.id, "full_name": b.full_name, "phone": b.phone, "status": b.status,
            "borrowed": _sum(bl.filter(date_issued__range=(start, end)), "principal"),
            "paid": _sum(live_payments().filter(loan__borrower=b, date__range=(start, end)), "amount"),
            "balance": _sum(bl.filter(status__in=OPEN_STATUSES), "balance"),
        })
    return rows


def _buckets(range_key: str, today: date):
    if range_key == "12m":
        out = []
        for y, m in iter_months(add_months_simple(today, -11), today):
            s, e = month_bounds(y, m)
            out.append((calendar.month_abbr[m], s, e))
        return out
    days = 30 if range_key == "30d" else 7
    return [((today - timedelta(days=i)).strftime("%a" if days == 7 else "%d %b"),
             today - timedelta(days=i), today - timedelta(days=i)) for i in range(days - 1, -1, -1)]


def add_months_simple(d: date, months: int) -> date:
    idx = d.month - 1 + months
    return date(d.year + idx // 12, idx % 12 + 1, 1)


def chart_series(range_key: str, today: date | None = None):
    today = today or timezone.localdate()
    series = []
    for label, s, e in _buckets(range_key, today):
        pays = live_payments().filter(date__range=(s, e))
        loans = live_loans().filter(date_issued__range=(s, e))
        series.append({
            "label": label, "start": s, "end": e,
            "lent": _sum(loans, "principal"),
            "collected": _sum(pays, "amount"),
            "profit": _sum(pays, F("interest_portion") + F("penalty_portion")) + _sum(loans, "processing_fee"),
        })
    return series


def overdue_items(limit=None):
    loans = (live_loans().filter(status__in=[Loan.Status.OVERDUE, Loan.Status.DEFAULTED])
             .select_related("borrower").prefetch_related("installments"))
    today = timezone.localdate()
    rows = []
    for l in loans:
        late = [i.due_date for i in l.installments.all() if i.status == Installment.Status.OVERDUE]
        rows.append({"loan_id": l.id, "reference": l.reference, "borrower": l.borrower.full_name,
                     "borrower_id": l.borrower_id, "phone": l.borrower.phone, "balance": l.balance,
                     "days_late": (today - min(late)).days if late else 0, "status": l.status})
    rows.sort(key=lambda r: -r["days_late"])
    return rows[:limit] if limit else rows


def dashboard_data(range_key="7d") -> dict:
    today = timezone.localdate()
    settings_obj = BusinessSettings.load()
    m_start, m_end = month_bounds(today.year, today.month)
    lm_end = m_start - timedelta(days=1)
    lm_start = lm_end.replace(day=1)
    this_month = period_summary(m_start, m_end)
    last_month = period_summary(lm_start, lm_end)
    year_summary = period_summary(date(today.year, 1, 1), date(today.year, 12, 31))

    open_loans = live_loans().filter(status__in=OPEN_STATUSES)
    overdue_insts = live_installments().filter(status=Installment.Status.OVERDUE)

    breakdown = []
    total_outstanding = _sum(open_loans, "balance")
    for status, label in [(Loan.Status.ACTIVE, "On time"), (Loan.Status.OVERDUE, "Overdue"),
                          (Loan.Status.DEFAULTED, "Defaulted"), (Loan.Status.WRITTEN_OFF, "Written off")]:
        qs = open_loans.filter(status=status)
        amount = _sum(qs, "balance")
        breakdown.append({"key": status, "label": label, "amount": amount, "count": qs.count(),
                          "percent": (amount / total_outstanding * 100).quantize(Decimal("1"))
                          if total_outstanding else 0})

    week_end = today + timedelta(days=7)
    due_week = (live_installments().filter(due_date__range=(today, week_end))
                .exclude(status=Installment.Status.PAID)
                .select_related("loan__borrower").annotate(left=REMAINING).order_by("due_date")[:12])

    overdue_list = overdue_items()

    recent = []
    for p in live_payments().select_related("loan__borrower").order_by("-date", "-id")[:8]:
        recent.append({"kind": "payment", "id": p.id, "loan_id": p.loan_id, "title": p.loan.borrower.full_name,
                       "subtitle": p.get_method_display(), "method": p.method, "date": p.date, "amount": p.amount,
                       "status": "Received"})
    for l in live_loans().select_related("borrower").order_by("-date_issued", "-id")[:8]:
        recent.append({"kind": "loan", "id": l.id, "loan_id": l.id, "title": l.borrower.full_name,
                       "subtitle": f"Loan {l.reference}", "reference": l.reference, "date": l.date_issued,
                       "amount": -l.principal, "status": "Given out"})
    recent.sort(key=lambda r: (r["date"], r["kind"] == "payment"), reverse=True)

    # "key" (plus count/rate) lets the frontend show each tip in the user's language.
    insights = []
    long_late = [o for o in overdue_list if o["days_late"] > 30]
    if long_late:
        insights.append({"key": "long_late", "count": len(long_late),
                         "title": f"{len(long_late)} loan{'s' if len(long_late) > 1 else ''} over 30 days late",
                         "body": "Follow up today. The longer a loan stays late, the harder it is to recover.",
                         "link": "/loans?status=overdue"})
    if this_month["collection_rate"] is not None and this_month["collection_rate"] < 80:
        insights.append({"key": "low_collection", "rate": this_month["collection_rate"],
                         "title": f"Collection rate is {this_month['collection_rate']}% this month",
                         "body": "Less than 80% of what was due has come in. Send reminders to borrowers due this week.",
                         "link": "/reports"})
    if settings_obj.monthly_lending_target and this_month["lent"] < settings_obj.monthly_lending_target:
        insights.append({"key": "target", "title": "Monthly lending target not reached yet",
                         "body": "Start this month's lending round to issue loans to many borrowers at once.",
                         "link": "/loans/round"})
    if not insights:
        insights.append({"key": "healthy", "title": "Everything looks healthy",
                         "body": "No loans are seriously late. Keep recording every payment the day it comes in.",
                         "link": "/reports"})

    data = {
        "today": today,
        "business_name": settings_obj.business_name,
        "currency_symbol": settings_obj.currency_symbol,
        "outstanding": total_outstanding,
        "lent_all_time": _sum(live_loans(), "principal"),
        "collected_all_time": _sum(live_payments(), "amount"),
        "lent_this_month": this_month["lent"],
        "lent_change": pct_change(this_month["lent"], last_month["lent"]),
        "collected_this_month": this_month["collected"],
        "collected_change": pct_change(this_month["collected"], last_month["collected"]),
        "collected_today": _sum(live_payments().filter(date=today), "amount"),
        "payments_today": live_payments().filter(date=today).count(),
        "profit_this_month": this_month["profit"],
        "profit_change": pct_change(this_month["profit"], last_month["profit"]),
        "expected_profit_this_month": this_month["expected_profit"],
        "profit_this_year": year_summary["profit"],
        "lent_this_year": year_summary["lent"],
        "profit_all_time": (_sum(live_payments(), F("interest_portion") + F("penalty_portion"))
                            + _sum(live_loans(), "processing_fee")),
        "active_borrowers": open_loans.values("borrower").distinct().count(),
        "total_borrowers": Borrower.objects.filter(is_active=True).count(),
        "overdue_count": open_loans.filter(status__in=[Loan.Status.OVERDUE, Loan.Status.DEFAULTED]).count(),
        "overdue_amount": _sum(overdue_insts, REMAINING),
        "collection_rate": this_month["collection_rate"],
        "due_this_month": this_month["due_amount"],
        "chart_range": range_key,
        "chart": chart_series(range_key, today),
        "portfolio": breakdown,
        "due_this_week": [{"loan_id": i.loan_id, "borrower": i.loan.borrower.full_name,
                           "borrower_id": i.loan.borrower_id, "due_date": i.due_date,
                           "amount": i.left, "reference": i.loan.reference} for i in due_week],
        "overdue_list": overdue_list[:8],
        "recent": recent[:8],
        "insights": insights,
        "targets": {
            "monthly_lending": {"target": settings_obj.monthly_lending_target, "actual": this_month["lent"]},
            "monthly_profit": {"target": settings_obj.monthly_profit_target, "actual": this_month["profit"]},
            "yearly_profit": {"target": settings_obj.yearly_profit_target, "actual": year_summary["profit"]},
            "yearly_lending": {"target": settings_obj.yearly_lending_target, "actual": year_summary["lent"]},
        },
    }
    return data
