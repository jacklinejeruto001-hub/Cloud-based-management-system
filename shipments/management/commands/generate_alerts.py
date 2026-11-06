from django.core.management.base import BaseCommand

from shipments import services


class Command(BaseCommand):
    help = (
        "Check every shipment record for quality problems (stale, overdue, missing waybill, "
        "awaiting delivery confirmation, flagged duplicates) and raise alerts. "
        "Schedule it (cron / Render cron job) e.g. every hour."
    )

    def handle(self, *args, **options):
        created = services.run_health_checks()
        self.stdout.write(self.style.SUCCESS(f"Health check complete: {created} new alert(s) created."))
