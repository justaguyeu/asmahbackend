"""Save a dated JSON backup of all data and keep the newest N.

    python manage.py backup_db            # keeps the last 30 backups
    python manage.py backup_db --keep 60

Schedule it daily, e.g. with cron:  0 2 * * * cd /path/backend && python manage.py backup_db
"""
from django.conf import settings
from django.core import management
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Write a JSON backup of the database to BACKUP_DIR"

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=30)

    def handle(self, *args, **opts):
        folder = settings.BACKUP_DIR
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"asmah-backup-{timezone.localtime():%Y%m%d-%H%M%S}.json"
        with path.open("w") as fh:
            management.call_command("dumpdata", "accounts", "lending", indent=1, stdout=fh)
        backups = sorted(folder.glob("asmah-backup-*.json"))
        for old in backups[:-opts["keep"]] if opts["keep"] else []:
            old.unlink()
        self.stdout.write(self.style.SUCCESS(f"Backup saved: {path}"))
