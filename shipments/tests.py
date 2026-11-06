"""
Automated tests for the rules the CBSRMS proposal promises:
deduplication, version control, real-time/consistent updates, delivery
confirmation, role-based access control, audit trail and alerts.

Run with:  python manage.py test
"""
import base64
import io
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile

from . import services
from .models import AuditLog, DeliveryConfirmation, Document, DocType, Notification, Shipment, Status

User = get_user_model()


def png_data_url():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (20, 10), (0, 0, 0, 255)).save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class BaseTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)

    def make_user(self, username, role, **extra):
        user = User.objects.create_user(username, f"{username}@example.com", "pass12345!", **extra)
        user.profile.role = role
        user.profile.save()
        return user

    def setUp(self):
        self.admin = self.make_user("admin", Profile.Role.ADMIN)
        self.manager = self.make_user("manager", Profile.Role.MANAGER)
        self.staff = self.make_user("staff", Profile.Role.STAFF)
        self.driver = self.make_user("driver", Profile.Role.DISPATCH)
        self.driver2 = self.make_user("driver2", Profile.Role.DISPATCH)
        self.client_user = self.make_user("client", Profile.Role.CLIENT)
        self.client2 = self.make_user("client2", Profile.Role.CLIENT)

    def login(self, user):
        self.client.force_login(user)

    def payload(self, **over):
        data = {
            "reference_number": "PO-1", "sender_name": "Acme Ltd", "sender_phone": "0700000000",
            "receiver_name": "Jane Mwangi", "receiver_phone": "0711111111", "receiver_email": "jane@example.com",
            "origin": "Nairobi", "destination": "Kisumu", "description": "20 laptops", "weight_kg": "45.5",
            "packages": "3", "expected_delivery": "", "client": "", "assigned_to": "",
        }
        data.update(over)
        return data

    def make_shipment(self, user=None, **over):
        values = dict(
            sender_name="Acme Ltd", receiver_name="Jane Mwangi", origin="Nairobi", destination="Kisumu",
            description="20 laptops", weight_kg=Decimal("45.50"), packages=3, created_by=user or self.staff,
        )
        values.update(over)
        shipment = Shipment(**values)
        services.save_with_version(shipment, user or self.staff, "Record created")
        return shipment


class CreateAndDuplicateTests(BaseTestCase):
    def test_create_generates_tracking_number_version_and_audit(self):
        self.login(self.staff)
        response = self.client.post(reverse("shipment_create"), self.payload())
        shipment = Shipment.objects.get()
        self.assertRedirects(response, shipment.get_absolute_url())
        self.assertTrue(shipment.tracking_number.startswith("CBS"))
        self.assertEqual(shipment.version, 1)
        self.assertEqual(shipment.versions.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="CREATE", username="staff").exists())

    def test_duplicate_is_blocked_until_confirmed_then_flagged(self):
        self.login(self.staff)
        self.client.post(reverse("shipment_create"), self.payload(reference_number="PO-9"))
        original = Shipment.objects.get()

        # same details again: warned, nothing saved
        response = self.client.post(reverse("shipment_create"), self.payload(reference_number="PO-9"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Possible duplicate detected")
        self.assertEqual(Shipment.objects.count(), 1)

        # confirmed: saved but flagged, managers are alerted
        self.client.post(reverse("shipment_create"), self.payload(reference_number="PO-9", confirm_duplicate="1"))
        self.assertEqual(Shipment.objects.count(), 2)
        dup = Shipment.objects.exclude(pk=original.pk).get()
        self.assertTrue(dup.is_flagged_duplicate)
        self.assertEqual(dup.duplicate_of, original)
        self.assertTrue(Notification.objects.filter(recipient=self.manager, kind="DUPLICATE").exists())

    def test_same_reference_for_same_sender_is_a_duplicate_even_if_details_differ(self):
        first = self.make_shipment(reference_number="INV-5")
        probe = Shipment(sender_name="acme ltd", receiver_name="Someone Else", origin="X", destination="Y",
                         description="different", weight_kg=Decimal("1"), packages=1, reference_number="inv-5")
        self.assertEqual(list(services.find_duplicates(probe)), [first])

    def test_fingerprint_ignores_case_and_spacing(self):
        a = self.make_shipment(description="20  Laptops")
        b = Shipment(sender_name=" ACME ltd", receiver_name="jane  mwangi", origin="nairobi", destination="KISUMU",
                     description="20 laptops", weight_kg=Decimal("45.5"), packages=3)
        self.assertEqual(list(services.find_duplicates(b)), [a])

    def test_manager_resolves_duplicate(self):
        original = self.make_shipment()
        dup = self.make_shipment()
        services.flag_duplicate(dup, original)
        self.login(self.manager)
        self.client.post(reverse("duplicate_resolve", args=[dup.pk]), {"action": "archive"})
        dup.refresh_from_db()
        self.assertTrue(dup.is_archived)
        self.assertFalse(dup.is_flagged_duplicate)

    def test_scan_duplicates_flags_near_matches(self):
        self.make_shipment(description="Electronics - 20 tablets")
        self.make_shipment(description="Electronics - 20 tablet")  # near-identical text
        self.assertEqual(services.scan_duplicates(), 1)
        self.assertEqual(Shipment.objects.filter(is_flagged_duplicate=True).count(), 1)


class VersionControlTests(BaseTestCase):
    def test_edit_creates_new_version_and_records_changes(self):
        shipment = self.make_shipment()
        self.login(self.staff)
        data = self.payload(destination="Eldoret", version=shipment.version)
        self.client.post(reverse("shipment_edit", args=[shipment.pk]), data)
        shipment.refresh_from_db()
        self.assertEqual(shipment.version, 2)
        v2 = shipment.versions.get(version_number=2)
        self.assertEqual(v2.changes["destination"], ["Kisumu", "Eldoret"])
        self.assertTrue(AuditLog.objects.filter(action="UPDATE", object_id=str(shipment.pk)).exists())

    def test_stale_edit_is_rejected_by_optimistic_locking(self):
        shipment = self.make_shipment()
        self.login(self.staff)
        self.client.post(reverse("shipment_edit", args=[shipment.pk]), self.payload(destination="Eldoret", version=1))
        # a second user still holding version 1 tries to save
        response = self.client.post(reverse("shipment_edit", args=[shipment.pk]), self.payload(destination="Garissa", version=1))
        self.assertContains(response, "changed by someone else")
        shipment.refresh_from_db()
        self.assertEqual(shipment.destination, "Eldoret")
        self.assertEqual(shipment.version, 2)

    def test_unchanged_edit_does_not_create_a_version(self):
        shipment = self.make_shipment()
        self.login(self.staff)
        data = self.payload(version=1, reference_number="", sender_phone="", receiver_phone="",
                            receiver_email="", weight_kg="45.50")
        self.client.post(reverse("shipment_edit", args=[shipment.pk]), data)
        shipment.refresh_from_db()
        self.assertEqual(shipment.version, 1)

    def test_restore_earlier_version(self):
        shipment = self.make_shipment()
        self.login(self.staff)
        self.client.post(reverse("shipment_edit", args=[shipment.pk]), self.payload(destination="Eldoret", version=1))
        self.client.post(reverse("version_restore", args=[shipment.pk, 1]))
        shipment.refresh_from_db()
        self.assertEqual(shipment.destination, "Kisumu")
        self.assertEqual(shipment.version, 3)  # restore is saved as a new version, history is kept

    def test_history_page_renders(self):
        shipment = self.make_shipment()
        self.login(self.manager)
        self.assertContains(self.client.get(reverse("shipment_versions", args=[shipment.pk])), "v1")


class DocumentTests(BaseTestCase):
    def upload(self, shipment, name, content, doc_type=DocType.WAYBILL, user=None):
        self.login(user or self.staff)
        return self.client.post(
            reverse("document_upload", args=[shipment.pk]),
            {"doc_type": doc_type, "title": "", "file": SimpleUploadedFile(name, content)},
            follow=True,
        )

    def test_identical_file_is_rejected(self):
        shipment = self.make_shipment()
        self.upload(shipment, "a.txt", b"same content")
        response = self.upload(shipment, "b.txt", b"same content")
        self.assertContains(response, "Duplicate file rejected")
        self.assertEqual(shipment.documents.count(), 1)

    def test_new_version_supersedes_old(self):
        shipment = self.make_shipment()
        self.upload(shipment, "wb1.txt", b"first")
        self.upload(shipment, "wb2.txt", b"second")
        docs = list(shipment.documents.order_by("version"))
        self.assertEqual([d.version for d in docs], [1, 2])
        self.assertEqual([d.is_current for d in docs], [False, True])
        # old version can be made current again
        self.client.post(reverse("document_current", args=[docs[0].pk]))
        docs[0].refresh_from_db()
        docs[1].refresh_from_db()
        self.assertTrue(docs[0].is_current)
        self.assertFalse(docs[1].is_current)

    def test_disallowed_extension_rejected(self):
        shipment = self.make_shipment()
        self.upload(shipment, "virus.exe", b"MZ...")
        self.assertEqual(shipment.documents.count(), 0)

    def test_download_requires_access_and_hides_internal_docs_from_clients(self):
        shipment = self.make_shipment(client=self.client_user)
        self.upload(shipment, "customs.txt", b"customs", doc_type=DocType.CUSTOMS)
        self.upload(shipment, "waybill.txt", b"waybill", doc_type=DocType.WAYBILL)
        customs, waybill = shipment.documents.get(doc_type="CUSTOMS"), shipment.documents.get(doc_type="WAYBILL")
        self.login(self.client_user)
        self.assertEqual(self.client.get(reverse("document_download", args=[customs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("document_download", args=[waybill.pk])).status_code, 200)
        self.login(self.client2)
        self.assertEqual(self.client.get(reverse("document_download", args=[waybill.pk])).status_code, 404)


class DeliveryConfirmationTests(BaseTestCase):
    def confirm(self, shipment, **over):
        data = {
            "received_by": "Jane Mwangi", "receiver_id_number": "123", "relationship": "Recipient",
            "delivered_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"), "notes": "",
            "signature_data": png_data_url(),
        }
        data.update(over)
        return self.client.post(reverse("delivery_confirm", args=[shipment.pk]), data)

    def test_delivered_cannot_be_set_without_proof(self):
        shipment = self.make_shipment(assigned_to=self.driver)
        self.login(self.driver)
        self.client.post(reverse("event_add", args=[shipment.pk]), {"status": "DELIVERED", "location": "", "note": ""})
        shipment.refresh_from_db()
        self.assertNotEqual(shipment.status, Status.DELIVERED)

    def test_confirmation_requires_signature_or_photo(self):
        shipment = self.make_shipment(assigned_to=self.driver)
        self.login(self.driver)
        response = self.confirm(shipment, signature_data="")
        self.assertContains(response, "needs a signature or a photo")
        self.assertFalse(DeliveryConfirmation.objects.exists())

    def test_confirmation_links_proof_and_closes_shipment(self):
        shipment = self.make_shipment(assigned_to=self.driver, client=self.client_user)
        self.login(self.driver)
        response = self.confirm(shipment)
        self.assertRedirects(response, reverse("pod_view", args=[shipment.pk]))
        shipment.refresh_from_db()
        pod = shipment.delivery_confirmation
        self.assertEqual(shipment.status, Status.DELIVERED)
        self.assertTrue(pod.signature)
        self.assertEqual(pod.confirmed_by, self.driver)
        self.assertEqual(shipment.events.first().status, Status.DELIVERED)
        self.assertTrue(Notification.objects.filter(recipient=self.manager, kind="DELIVERED").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.client_user, kind="DELIVERED").exists())
        self.assertTrue(AuditLog.objects.filter(action="CONFIRM").exists())
        # POD page and its image are viewable by the owner
        self.assertEqual(self.client.get(reverse("pod_view", args=[shipment.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("pod_file", args=[pod.pk, "signature"])).status_code, 200)

    def test_second_confirmation_is_rejected(self):
        shipment = self.make_shipment(assigned_to=self.driver)
        self.login(self.driver)
        self.confirm(shipment)
        self.confirm(shipment, received_by="Someone Else")
        self.assertEqual(DeliveryConfirmation.objects.count(), 1)
        self.assertEqual(shipment.delivery_confirmation.received_by, "Jane Mwangi")

    def test_future_delivery_time_rejected(self):
        shipment = self.make_shipment(assigned_to=self.driver)
        self.login(self.driver)
        future = (timezone.localtime() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        self.confirm(shipment, delivered_at=future)
        self.assertFalse(DeliveryConfirmation.objects.exists())

    def test_client_cannot_see_other_clients_pod(self):
        shipment = self.make_shipment(assigned_to=self.driver, client=self.client_user)
        self.login(self.driver)
        self.confirm(shipment)
        pod = shipment.delivery_confirmation
        self.login(self.client2)
        self.assertEqual(self.client.get(reverse("pod_file", args=[pod.pk, "signature"])).status_code, 404)


class AccessControlTests(BaseTestCase):
    def test_anonymous_is_redirected_to_login(self):
        for name in ("dashboard", "shipment_list", "shipment_create", "reports", "audit_log", "notifications"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn("/accounts/login/", response["Location"])

    def test_dispatcher_cannot_register_or_edit(self):
        shipment = self.make_shipment(assigned_to=self.driver)
        self.login(self.driver)
        self.assertEqual(self.client.get(reverse("shipment_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("shipment_edit", args=[shipment.pk])).status_code, 403)

    def test_dispatcher_sees_only_assigned_shipments(self):
        mine = self.make_shipment(assigned_to=self.driver)
        other = self.make_shipment(assigned_to=self.driver2, description="other")
        self.login(self.driver)
        self.assertEqual(self.client.get(mine.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(other.get_absolute_url()).status_code, 404)
        body = self.client.get(reverse("shipment_list")).content.decode()
        self.assertIn(mine.tracking_number, body)
        self.assertNotIn(other.tracking_number, body)

    def test_client_sees_only_own_shipments_and_cannot_modify(self):
        mine = self.make_shipment(client=self.client_user)
        other = self.make_shipment(client=self.client2, description="other")
        self.login(self.client_user)
        self.assertEqual(self.client.get(mine.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(other.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(reverse("shipment_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("shipment_versions", args=[mine.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("event_add", args=[mine.pk]), {"status": "IN_TRANSIT"}).status_code, 403)

    def test_staff_cannot_archive_or_open_reports_or_audit(self):
        shipment = self.make_shipment()
        self.login(self.staff)
        self.assertEqual(self.client.post(reverse("shipment_archive", args=[shipment.pk])).status_code, 403)
        for name in ("reports", "audit_log", "duplicates", "report_csv"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)

    def test_manager_can_archive_restore_and_see_management_pages(self):
        shipment = self.make_shipment()
        self.login(self.manager)
        self.client.post(reverse("shipment_archive", args=[shipment.pk]), {"reason": "test"})
        shipment.refresh_from_db()
        self.assertTrue(shipment.is_archived)
        self.assertNotContains(self.client.get(reverse("shipment_list")), f'href="{shipment.get_absolute_url()}"')
        self.assertContains(self.client.get(reverse("shipment_list") + "?archived=1"), f'href="{shipment.get_absolute_url()}"')
        self.client.post(reverse("shipment_restore", args=[shipment.pk]))
        shipment.refresh_from_db()
        self.assertFalse(shipment.is_archived)
        for name in ("reports", "audit_log", "duplicates", "dashboard", "notifications", "report_csv"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertTrue(AuditLog.objects.filter(action="ARCHIVE").exists())
        self.assertTrue(AuditLog.objects.filter(action="RESTORE").exists())

    def test_only_admin_manages_users(self):
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 200)
        response = self.client.post(reverse("accounts:user_create"), {
            "username": "newdriver", "first_name": "N", "last_name": "D", "email": "n@example.com",
            "role": "DISPATCH", "phone": "", "company": "", "password1": "Str0ng!Pass#9", "password2": "Str0ng!Pass#9",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(username="newdriver").profile.role, Profile.Role.DISPATCH)

    def test_get_not_allowed_on_state_changing_views(self):
        shipment = self.make_shipment()
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse("shipment_archive", args=[shipment.pk])).status_code, 405)


class AuditTrailTests(BaseTestCase):
    def test_login_logout_and_failed_login_are_logged(self):
        self.client.post(reverse("accounts:login"), {"username": "staff", "password": "pass12345!"})
        self.client.post(reverse("accounts:logout"))
        self.client.post(reverse("accounts:login"), {"username": "staff", "password": "wrong"})
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"LOGIN", "LOGOUT", "LOGIN_FAILED"} <= actions)

    def test_audit_entries_are_immutable(self):
        services.write_audit("CREATE", model_name="X", object_repr="x", user=self.staff)
        entry = AuditLog.objects.get()
        with self.assertRaises(PermissionError):
            entry.object_repr = "tampered"
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_status_change_is_audited_with_acting_user(self):
        shipment = self.make_shipment(assigned_to=self.driver)
        self.login(self.driver)
        self.client.post(reverse("event_add", args=[shipment.pk]), {"status": "IN_TRANSIT", "location": "Nakuru", "note": ""})
        entry = AuditLog.objects.filter(action="STATUS").first()
        self.assertEqual(entry.username, "driver")
        self.assertEqual(entry.changes["status"], ["REGISTERED", "IN_TRANSIT"])


class AlertTests(BaseTestCase):
    def test_health_checks_flag_stale_overdue_and_missing_waybill_without_spamming(self):
        today = timezone.localdate()
        s = self.make_shipment(assigned_to=self.driver, expected_delivery=today - timedelta(days=2))
        Shipment.objects.filter(pk=s.pk).update(
            last_activity=timezone.now() - timedelta(days=3), created_at=timezone.now() - timedelta(days=3)
        )
        first = services.run_health_checks()
        self.assertGreater(first, 0)
        kinds = set(Notification.objects.values_list("kind", flat=True))
        self.assertTrue({"STALE", "OVERDUE", "NO_WAYBILL"} <= kinds)
        self.assertTrue(Notification.objects.filter(recipient=self.driver).exists())
        self.assertEqual(services.run_health_checks(), 0)  # same alerts are not repeated within 24h

    def test_healthy_shipment_raises_no_alerts(self):
        s = self.make_shipment()
        services.upload_document(s, SimpleUploadedFile("w.txt", b"wb"), DocType.WAYBILL, "", self.staff)
        self.assertEqual(services.run_health_checks(), 0)
        self.assertEqual(services.shipment_issues(s), [])

    def test_failed_delivery_alerts_managers(self):
        s = self.make_shipment(assigned_to=self.driver)
        services.add_event(s, Status.FAILED, self.driver, "Kisumu", "Customer not available")
        self.assertTrue(Notification.objects.filter(recipient=self.manager, kind="FAILED").exists())

    def test_opening_notification_marks_it_read(self):
        s = self.make_shipment()
        services.notify([self.staff], "SYSTEM", "hello", link=services.shipment_link(s))
        note = Notification.objects.get(recipient=self.staff)
        self.login(self.staff)
        response = self.client.get(reverse("notification_open", args=[note.pk]))
        self.assertRedirects(response, s.get_absolute_url())
        note.refresh_from_db()
        self.assertTrue(note.is_read)

    def test_cannot_open_someone_elses_notification(self):
        services.notify([self.staff], "SYSTEM", "private")
        note = Notification.objects.get()
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse("notification_open", args=[note.pk])).status_code, 404)


class TrackingAndApiTests(BaseTestCase):
    def test_public_tracking_masks_personal_data(self):
        s = self.make_shipment(receiver_name="Jane Mwangi")
        response = self.client.get(reverse("track_result", args=[s.tracking_number.lower()]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, s.tracking_number)
        self.assertContains(response, "J*** M*****")
        self.assertNotContains(response, "Jane Mwangi")
        self.assertNotContains(response, "0711")

    def test_unknown_or_archived_tracking_number_returns_404(self):
        s = self.make_shipment()
        services.archive_shipment(s, self.manager)
        self.assertEqual(self.client.get(reverse("track_result", args=[s.tracking_number])).status_code, 404)
        self.assertEqual(self.client.get(reverse("track_result", args=["NOPE"])).status_code, 404)

    def test_state_api_changes_when_record_changes(self):
        s = self.make_shipment(assigned_to=self.driver)
        self.login(self.manager)
        url = reverse("api_shipment_state", args=[s.pk])
        before = self.client.get(url).json()
        services.add_event(s, Status.IN_TRANSIT, self.driver, "Nakuru")
        after = self.client.get(url).json()
        self.assertNotEqual(before, after)
        self.assertEqual(after["status"], "IN_TRANSIT")
        self.assertEqual(after["version"], before["version"] + 1)

    def test_state_api_respects_scoping(self):
        s = self.make_shipment(assigned_to=self.driver)
        self.login(self.driver2)
        self.assertEqual(self.client.get(reverse("api_shipment_state", args=[s.pk])).status_code, 404)

    def test_dashboard_and_notification_apis(self):
        self.make_shipment()
        self.login(self.manager)
        data = self.client.get(reverse("api_dashboard")).json()
        self.assertEqual(data["cards"]["total"], 1)
        self.assertIn("issues", data)
        self.assertIn("unread", self.client.get(reverse("api_notifications")).json())
        self.assertIn("count", self.client.get(reverse("api_pulse")).json())

    def test_csv_export_neutralises_formula_injection(self):
        self.make_shipment(receiver_name="=HYPERLINK(\"http://evil\")")
        self.login(self.manager)
        response = self.client.get(reverse("report_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("'=HYPERLINK", response.content.decode())

    def test_dashboard_value_for_each_role_renders(self):
        self.make_shipment(assigned_to=self.driver, client=self.client_user)
        for user in (self.admin, self.manager, self.staff, self.driver, self.client_user):
            self.login(user)
            self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200, user.username)
            self.assertEqual(self.client.get(reverse("shipment_list")).status_code, 200, user.username)
