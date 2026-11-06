"""Create demo users and realistic sample shipments so every screen has data."""
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Profile
from shipments import services
from shipments.models import DocType, Shipment, Status

User = get_user_model()

USERS = [
    # username, first, last, role, company
    ("admin1", "Alice", "Admin", Profile.Role.ADMIN, ""),
    ("manager1", "Mark", "Manager", Profile.Role.MANAGER, ""),
    ("staff1", "Sarah", "Operations", Profile.Role.STAFF, ""),
    ("driver1", "David", "Driver", Profile.Role.DISPATCH, ""),
    ("client1", "Grace", "Wanjiru", Profile.Role.CLIENT, "Savannah Traders Ltd"),
]


def signature_png():
    """A small hand-drawn-looking signature image (needs Pillow)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (400, 160), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.line([(20, 110), (70, 40), (110, 120), (160, 50), (210, 115), (300, 70), (380, 100)], fill=(15, 42, 67, 255), width=4)
    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    return ContentFile(buffer.getvalue(), name="signature.png")


class Command(BaseCommand):
    help = "Load demo users and sample shipments."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Demo@12345", help="Password for all demo accounts.")

    def handle(self, *args, **opts):
        if Shipment.objects.exists():
            self.stdout.write(self.style.WARNING("Shipments already exist - skipping demo data."))
            return
        users = {}
        for username, first, last, role, company in USERS:
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last, "email": f"{username}@example.com", "is_staff": role == Profile.Role.ADMIN},
            )
            user.set_password(opts["password"])
            user.save()
            profile = user.profile
            profile.role, profile.company = role, company
            profile.save()
            users[username] = user

        staff, manager, driver, client = users["staff1"], users["manager1"], users["driver1"], users["client1"]
        today = timezone.localdate()

        def make(sender, receiver, origin, dest, desc, weight, pkgs, expected_days=3, ref="", assigned=driver, with_client=True):
            s = Shipment(
                sender_name=sender, receiver_name=receiver, origin=origin, destination=dest, description=desc,
                weight_kg=Decimal(str(weight)), packages=pkgs, reference_number=ref, assigned_to=assigned,
                client=client if with_client else None, created_by=staff, receiver_phone="0712 345 678",
                receiver_email="receiver@example.com", expected_delivery=today + timedelta(days=expected_days),
            )
            services.save_with_version(s, staff, "Record created")
            return s

        def waybill(s, text=None):
            f = SimpleUploadedFile(f"waybill_{s.tracking_number}.txt", (text or f"WAYBILL {s.tracking_number}").encode())
            services.upload_document(s, f, DocType.WAYBILL, "", staff)

        s1 = make("Savannah Traders", "Joseph Otieno", "Nairobi", "Kisumu", "Electronics - 20 tablets", 45, 3, ref="PO-1001")
        waybill(s1)
        services.add_event(s1, Status.PICKED_UP, staff, "Nairobi depot", "Collected from sender")
        services.add_event(s1, Status.IN_TRANSIT, driver, "Nakuru", "On the Nairobi-Kisumu route")

        s2 = make("Savannah Traders", "Mary Achieng", "Nairobi", "Mombasa", "Textile rolls", 210, 12, expected_days=2, ref="PO-1002")
        waybill(s2)
        services.add_event(s2, Status.PICKED_UP, staff, "Nairobi depot")
        services.add_event(s2, Status.OUT_FOR_DELIVERY, driver, "Mombasa hub", "With driver")

        s3 = make("Rift Valley Foods", "Peter Kamau", "Nakuru", "Nairobi", "Packaged milk, 40 crates", 800, 40, expected_days=-1)
        waybill(s3)
        services.add_event(s3, Status.IN_TRANSIT, driver, "Naivasha")

        s4 = make("Savannah Traders", "Linet Chebet", "Nairobi", "Eldoret", "Office furniture", 150, 6, ref="PO-1004")
        waybill(s4)
        services.add_event(s4, Status.PICKED_UP, staff, "Nairobi depot")
        services.add_event(s4, Status.IN_TRANSIT, driver, "Nakuru")
        services.add_event(s4, Status.OUT_FOR_DELIVERY, driver, "Eldoret")
        services.confirm_delivery(
            s4, driver, received_by="Linet Chebet", receiver_id_number="23456789", relationship="Recipient",
            signature=signature_png(), delivered_at=timezone.now() - timedelta(hours=3),
        )

        s5 = make("Coastal Spices", "Hassan Ali", "Mombasa", "Nairobi", "Spices - 30 sacks", 600, 30, ref="CS-77")
        waybill(s5)
        services.add_event(s5, Status.PICKED_UP, staff, "Mombasa port")
        services.confirm_delivery(
            s5, driver, received_by="Hassan Ali", relationship="Recipient", signature=signature_png(),
            delivered_at=timezone.now() - timedelta(days=1),
        )

        s6 = make("Rift Valley Foods", "Ann Njeri", "Nakuru", "Thika", "Fresh produce", 90, 9, with_client=False)
        # (no waybill uploaded -> will be reported as a missing document after the grace period)
        Shipment.objects.filter(pk=s6.pk).update(created_at=timezone.now() - timedelta(days=2))

        # a duplicate of s1 (same details) entered a second time
        dup = make("Savannah Traders", "Joseph Otieno", "Nairobi", "Kisumu", "Electronics - 20 tablets", 45, 3, ref="PO-1001")
        services.flag_duplicate(dup, s1)

        # a stale record: no update for 3 days
        s8 = make("Lakeview Pharma", "Dr. Okoth", "Kisumu", "Nairobi", "Medical supplies", 35, 4, expected_days=1, assigned=driver)
        waybill(s8)
        services.add_event(s8, Status.IN_TRANSIT, driver, "Kericho")
        Shipment.objects.filter(pk=s8.pk).update(last_activity=timezone.now() - timedelta(days=3))

        # a document with two versions to show version control
        newer = SimpleUploadedFile("invoice_v1.txt", b"Invoice draft")
        services.upload_document(s1, newer, DocType.INVOICE, "", staff)
        newer2 = SimpleUploadedFile("invoice_v2.txt", b"Invoice final - corrected amount")
        services.upload_document(s1, newer2, DocType.INVOICE, "", staff)

        services.run_health_checks()
        self.stdout.write(self.style.SUCCESS(
            f"Demo data loaded. Accounts (password '{opts['password']}'): "
            + ", ".join(u[0] for u in USERS)
        ))
