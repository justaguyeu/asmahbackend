"""Re-check every open loan: overdue status, penalties, defaults.
Run daily (the app also does this automatically once a day when opened)."""
from django.core.management.base import BaseCommand

from lending.services import refresh_open_loans


class Command(BaseCommand):
    help = "Update statuses and penalties of all open loans"

    def handle(self, *args, **opts):
        count = refresh_open_loans(force=True)
        self.stdout.write(self.style.SUCCESS(f"Refreshed {count} open loans."))
