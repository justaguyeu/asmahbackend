"""
Reporting. Every profit figure comes from money actually received:
interest + penalties collected in payments, plus processing fees on loans
issued in the period (fees are deducted when the money is handed out).
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from .models import ZERO, Borrower, BusinessSettings, Installment, Loan, Payment, with_status

DEC = DecimalField(max_digits=16, decimal_places=2)
OPEN_STATUSES = [Loan.Status.ACTIVE, Loan.Status.OVERDUE, Loan.Status.DEFAULTED, Loan.Status.WRITTEN_OFF]
LOST_STATUSES = [Loan.Status.DEFAULTED, Loan.Status.WRITTEN_OFF]


def _money(expr, when=None):
    """A sum of money for aggregate()/annotate(): 0 rather than None when nothing matches.
    Several of these in one aggregate() cost one database query instead of one each."""
    return Coalesce(Sum(expr, filter=when, output_field=DEC), ZERO, output_field=DEC)


def _sum(qs, expr):
    return qs.aggregate(v=_money(expr))["v"]


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
    # one query per table for all the figures (reports call this once per month or year)
    lo = live_loans().filter(date_issued__range=(start, end)).aggregate(
        lent=_money("principal"), fees=_money("processing_fee"), count=Count("id"),
        outstanding=_money("balance", Q(status__in=OPEN_STATUSES)),
        lost=_money("balance", Q(status__in=LOST_STATUSES)),
        borrowers=Count("borrower", distinct=True))
    pa = live_payments().filter(date__range=(start, end)).aggregate(
        interest=_money("interest_portion"), penalties=_money("penalty_portion"),
        principal=_money("principal_portion"), collected=_money("amount"), count=Count("id"))
    ins = live_installments().filter(due_date__range=(start, end)).aggregate(
        expected_interest=_money("interest_due"),
        due=_money(F("principal_due") + F("interest_due")),
        paid=_money(F("principal_paid") + F("interest_paid")),
        overdue=_money(REMAINING, Q(status=Installment.Status.OVERDUE)))
    fees, penalties, due_pi = lo["fees"], pa["penalties"], ins["due"]

    return {
        "start": start, "end": end,
        "lent": lo["lent"],
        "loans_issued": lo["count"],
        "collected": pa["collected"],
        "principal_collected": pa["principal"],
        "interest_collected": pa["interest"],
        "penalties_collected": penalties,
        "fees_collected": fees,
        "fees_and_penalties": fees + penalties,
        "profit": pa["interest"] + penalties + fees,
        "expected_interest": ins["expected_interest"],
        "expected_profit": ins["expected_interest"] + fees,
        "outstanding": lo["outstanding"],
        "overdue_amount": ins["overdue"],
        "lost_amount": lo["lost"],
        "due_amount": due_pi,
        "collection_rate": (ins["paid"] / due_pi * 100).quantize(Decimal("0.1")) if due_pi else None,
        "new_borrowers": Borrower.objects.filter(created_at__date__range=(start, end)).count(),
        "borrowers_served": lo["borrowers"],
        "payments_count": pa["count"],
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
             .annotate(left=REMAINING).order_by("due_date", "id"))  # id: same order on every load
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

    # one grouped query per figure for all borrowers, not three queries per borrower
    def per_borrower(qs, key, expr):
        return dict(qs.order_by().values(key).annotate(v=_money(expr)).values_list(key, "v"))

    borrowed = per_borrower(live_loans().filter(borrower_id__in=ids, date_issued__range=(start, end)),
                            "borrower_id", "principal")
    paid = per_borrower(live_payments().filter(loan__borrower_id__in=ids, date__range=(start, end)),
                        "loan__borrower_id", "amount")
    balance = per_borrower(live_loans().filter(borrower_id__in=ids, status__in=OPEN_STATUSES),
                           "borrower_id", "balance")
    return [{"id": b.id, "full_name": b.full_name, "phone": b.phone, "status": b.status,
             "borrowed": borrowed.get(b.id, ZERO), "paid": paid.get(b.id, ZERO), "balance": balance.get(b.id, ZERO)}
            for b in with_status(Borrower.objects.filter(id__in=ids))]


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
    buckets = _buckets(range_key, today)
    first, last = buckets[0][1], buckets[-1][2]
    # two grouped queries for the whole chart (by day, or by month for 12m), not four per bar
    by_month = range_key == "12m"

    def grouped(qs, field, **sums):
        bar = TruncMonth(field) if by_month else F(field)
        return {r["bar"]: r for r in qs.order_by().annotate(bar=bar).values("bar").annotate(**sums)}

    pays = grouped(live_payments().filter(date__range=(first, last)), "date", collected=_money("amount"),
                   profit=_money(F("interest_portion") + F("penalty_portion")))
    loans = grouped(live_loans().filter(date_issued__range=(first, last)), "date_issued",
                    lent=_money("principal"), fees=_money("processing_fee"))
    series = []
    for label, s, e in buckets:
        p, lo = pays.get(s, {}), loans.get(s, {})
        series.append({
            "label": label, "start": s, "end": e,
            "lent": lo.get("lent", ZERO),
            "collected": p.get("collected", ZERO),
            "profit": p.get("profit", ZERO) + lo.get("fees", ZERO),
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

    # every open status in one grouped query
    by_status = {r["status"]: r for r in open_loans.order_by().values("status")
                 .annotate(amount=_money("balance"), count=Count("id"))}
    total_outstanding = sum((r["amount"] for r in by_status.values()), ZERO)
    breakdown = []
    for status, label in [(Loan.Status.ACTIVE, "On time"), (Loan.Status.OVERDUE, "Overdue"),
                          (Loan.Status.DEFAULTED, "Defaulted"), (Loan.Status.WRITTEN_OFF, "Written off")]:
        amount, count = by_status.get(status, {}).get("amount", ZERO), by_status.get(status, {}).get("count", 0)
        breakdown.append({"key": status, "label": label, "amount": amount, "count": count,
                          "percent": (amount / total_outstanding * 100).quantize(Decimal("1"))
                          if total_outstanding else 0})
    all_loans = live_loans().aggregate(lent=_money("principal"), fees=_money("processing_fee"))
    all_pays = live_payments().aggregate(
        collected=_money("amount"), profit=_money(F("interest_portion") + F("penalty_portion")),
        today=_money("amount", Q(date=today)), today_count=Count("id", filter=Q(date=today)))
    open_counts = open_loans.aggregate(
        borrowers=Count("borrower", distinct=True),
        overdue=Count("id", filter=Q(status__in=[Loan.Status.OVERDUE, Loan.Status.DEFAULTED])))

    week_end = today + timedelta(days=7)
    due_week = (live_installments().filter(due_date__range=(today, week_end))
                .exclude(status=Installment.Status.PAID)
                .select_related("loan__borrower").annotate(left=REMAINING).order_by("due_date", "id")[:12])

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
        "lent_all_time": all_loans["lent"],
        "collected_all_time": all_pays["collected"],
        "lent_this_month": this_month["lent"],
        "lent_change": pct_change(this_month["lent"], last_month["lent"]),
        "collected_this_month": this_month["collected"],
        "collected_change": pct_change(this_month["collected"], last_month["collected"]),
        "collected_today": all_pays["today"],
        "payments_today": all_pays["today_count"],
        "profit_this_month": this_month["profit"],
        "profit_change": pct_change(this_month["profit"], last_month["profit"]),
        "expected_profit_this_month": this_month["expected_profit"],
        "profit_this_year": year_summary["profit"],
        "lent_this_year": year_summary["lent"],
        "profit_all_time": all_pays["profit"] + all_loans["fees"],
        "active_borrowers": open_counts["borrowers"],
        "total_borrowers": Borrower.objects.filter(is_active=True).count(),
        "overdue_count": open_counts["overdue"],
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
