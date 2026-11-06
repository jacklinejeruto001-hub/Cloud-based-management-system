from django.core.management.base import BaseCommand

from shipments import services


class Command(BaseCommand):
    help = "Scan all active shipment records for duplicates and flag them for manager review."

    def handle(self, *args, **options):
        flagged = services.scan_duplicates()
        self.stdout.write(self.style.SUCCESS(f"Duplicate scan complete: {flagged} record(s) flagged."))
