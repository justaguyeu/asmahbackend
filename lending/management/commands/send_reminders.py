"""Payment reminders (SMS / WhatsApp), ready to connect to a provider.

    python manage.py send_reminders --dry-run   # just print the messages

To send real SMS, implement `send_sms()` below with your provider
(e.g. Beem Africa, Africa's Talking, NextSMS) and schedule this daily.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from lending.activity import log_activity
from lending.models import BusinessSettings, Installment
from lending.reports import REMAINING, live_installments


def send_sms(phone: str, message: str) -> bool:  # pragma: no cover - provider specific
    raise NotImplementedError("Connect an SMS provider in lending/management/commands/send_reminders.py")


class Command(BaseCommand):
    help = "Send reminders before due dates and when payments are late"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        s = BusinessSettings.load()
        if not s.reminders_enabled and not opts["dry_run"]:
            self.stdout.write("Reminders are turned off in Settings.")
            return
        today = timezone.localdate()
        soon = today + timedelta(days=s.reminder_days_before)
        qs = (live_installments().exclude(status=Installment.Status.PAID)
              .filter(due_date__lte=soon).select_related("loan__borrower").annotate(left=REMAINING))
        sent = 0
        for inst in qs:
            b = inst.loan.borrower
            if not b.phone or inst.left <= 0:
                continue
            if inst.due_date >= today:
                msg = (f"Habari {b.full_name.split()[0]}, kumbusho: malipo ya {s.currency_symbol} {inst.left:,.0f} "
                       f"kwa mkopo {inst.loan.reference} yanatakiwa {inst.due_date:%d/%m/%Y}. Asante - {s.business_name}")
            else:
                days = (today - inst.due_date).days
                msg = (f"Habari {b.full_name.split()[0]}, malipo yako ya {s.currency_symbol} {inst.left:,.0f} "
                       f"(mkopo {inst.loan.reference}) yamechelewa siku {days}. Tafadhali lipa leo. - {s.business_name}")
            if opts["dry_run"]:
                self.stdout.write(f"{b.phone}: {msg}")
            else:
                send_sms(b.phone, msg)
            sent += 1
        if not opts["dry_run"]:
            log_activity(None, "reminders_sent", "reminder", "", f"Sent {sent} reminders")
        self.stdout.write(self.style.SUCCESS(f"{sent} reminder(s) {'listed' if opts['dry_run'] else 'sent'}."))
