import secrets
import string
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from .utils import compute_fingerprint

User = settings.AUTH_USER_MODEL


class Status(models.TextChoices):
    REGISTERED = "REGISTERED", "Registered"
    PICKED_UP = "PICKED_UP", "Picked up"
    IN_TRANSIT = "IN_TRANSIT", "In transit"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY", "Out for delivery"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Delivery failed"
    CANCELLED = "CANCELLED", "Cancelled"


FINAL_STATUSES = (Status.DELIVERED, Status.CANCELLED)


def generate_tracking_number():
    alphabet = string.ascii_uppercase + string.digits
    stamp = timezone.now().strftime("%y%m%d")
    for _ in range(25):
        candidate = f"CBS{stamp}" + "".join(secrets.choice(alphabet) for _ in range(5))
        if not Shipment.objects.filter(tracking_number=candidate).exists():
            return candidate
    raise RuntimeError("Could not generate a unique tracking number")


class Shipment(models.Model):
    tracking_number = models.CharField(max_length=20, unique=True, editable=False)
    reference_number = models.CharField(
        max_length=60, blank=True, db_index=True, help_text="Client PO / waybill / invoice reference (optional)."
    )

    sender_name = models.CharField(max_length=120)
    sender_phone = models.CharField(max_length=30, blank=True)
    receiver_name = models.CharField(max_length=120)
    receiver_phone = models.CharField(max_length=30, blank=True)
    receiver_email = models.EmailField(blank=True)

    origin = models.CharField(max_length=120)
    destination = models.CharField(max_length=120)
    description = models.TextField(help_text="What is being shipped.")
    weight_kg = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0.00"))
    packages = models.PositiveIntegerField(default=1)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REGISTERED, db_index=True)
    expected_delivery = models.DateField(null=True, blank=True)

    client = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="client_shipments",
        help_text="Client account that may track this shipment online.",
    )
    assigned_to = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_shipments",
        help_text="Dispatcher / driver responsible for delivery.",
    )
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="created_shipments")

    # --- data-quality controls ------------------------------------------------
    fingerprint = models.CharField(max_length=64, db_index=True, editable=False, blank=True)
    is_flagged_duplicate = models.BooleanField(default=False, db_index=True)
    duplicate_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates")
    duplicate_reviewed = models.BooleanField(default=False)

    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    version = models.PositiveIntegerField(default=1, editable=False)
    last_activity = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "is_archived"])]

    def __str__(self):
        return self.tracking_number

    def get_absolute_url(self):
        return reverse("shipment_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.tracking_number:
            self.tracking_number = generate_tracking_number()
        self.fingerprint = compute_fingerprint(
            self.sender_name, self.receiver_name, self.origin, self.destination,
            self.description, self.weight_kg, self.packages,
        )
        super().save(*args, **kwargs)

    @property
    def is_final(self):
        return self.status in FINAL_STATUSES

    @property
    def is_overdue(self):
        return bool(self.expected_delivery and not self.is_final and self.expected_delivery < timezone.localdate())

    @property
    def has_pod(self):
        return hasattr(self, "delivery_confirmation")


class ShipmentEvent(models.Model):
    """One entry in the shipment's tracking timeline (every status update)."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=20, choices=Status.choices)
    location = models.CharField(max_length=160, blank=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.shipment} -> {self.get_status_display()}"


class ShipmentVersion(models.Model):
    """Immutable snapshot of a shipment after each change (version control)."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    data = models.JSONField()
    changes = models.JSONField(default=dict, blank=True)
    summary = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-version_number"]
        unique_together = [("shipment", "version_number")]

    def __str__(self):
        return f"{self.shipment} v{self.version_number}"


class DocType(models.TextChoices):
    WAYBILL = "WAYBILL", "Waybill"
    INVOICE = "INVOICE", "Invoice"
    PACKING = "PACKING", "Packing list"
    CUSTOMS = "CUSTOMS", "Customs document"
    POD = "POD", "Proof of delivery (scan)"
    OTHER = "OTHER", "Other"


CLIENT_VISIBLE_DOC_TYPES = (DocType.WAYBILL, DocType.INVOICE, DocType.POD)


def document_path(instance, filename):
    return f"documents/{timezone.now():%Y/%m}/{secrets.token_hex(8)}_{filename}"


class Document(models.Model):
    """A file attached to a shipment. Re-uploading creates a new version and
    marks the older one as superseded (outdated) instead of overwriting it."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=10, choices=DocType.choices, default=DocType.OTHER)
    title = models.CharField(max_length=160, blank=True)
    file = models.FileField(upload_to=document_path)
    original_name = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64, db_index=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True, db_index=True)
    uploaded_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(default=timezone.now)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["doc_type", "title", "-version"]

    def __str__(self):
        return f"{self.get_doc_type_display()} v{self.version} ({self.shipment})"

    @property
    def display_title(self):
        return self.title or self.get_doc_type_display()


class DeliveryConfirmation(models.Model):
    """Proof of delivery, linked one-to-one to the shipment."""

    shipment = models.OneToOneField(Shipment, on_delete=models.CASCADE, related_name="delivery_confirmation")
    received_by = models.CharField(max_length=120)
    receiver_id_number = models.CharField(max_length=40, blank=True)
    relationship = models.CharField(max_length=60, blank=True, help_text="e.g. Recipient, Security guard, Reception")
    signature = models.ImageField(upload_to="pod/signatures/%Y/%m/", blank=True)
    photo = models.ImageField(upload_to="pod/photos/%Y/%m/", blank=True)
    notes = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivered_at = models.DateTimeField(default=timezone.now)
    confirmed_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"POD for {self.shipment}"


class Notification(models.Model):
    class Kind(models.TextChoices):
        STALE = "STALE", "Missing update"
        OVERDUE = "OVERDUE", "Overdue"
        AWAITING_POD = "AWAITING_POD", "Awaiting confirmation"
        NO_WAYBILL = "NO_WAYBILL", "Missing document"
        DUPLICATE = "DUPLICATE", "Possible duplicate"
        DELIVERED = "DELIVERED", "Delivered"
        FAILED = "FAILED", "Delivery failed"
        ASSIGNED = "ASSIGNED", "Assignment"
        SYSTEM = "SYSTEM", "System"

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=15, choices=Kind.choices, default=Kind.SYSTEM)
    message = models.CharField(max_length=300)
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    link = models.CharField(max_length=300, blank=True)
    dedupe_key = models.CharField(max_length=80, blank=True, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.recipient}: {self.message[:40]}"


class AuditLog(models.Model):
    """Append-only record of who did what and when."""

    class Action(models.TextChoices):
        CREATE = "CREATE", "Created"
        UPDATE = "UPDATE", "Updated"
        DELETE = "DELETE", "Deleted"
        ARCHIVE = "ARCHIVE", "Archived"
        RESTORE = "RESTORE", "Restored"
        UPLOAD = "UPLOAD", "Uploaded"
        CONFIRM = "CONFIRM", "Delivery confirmed"
        STATUS = "STATUS", "Status update"
        LOGIN = "LOGIN", "Signed in"
        LOGOUT = "LOGOUT", "Signed out"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed sign-in"

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=15, choices=Action.choices, db_index=True)
    model_name = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.username} {self.action} {self.object_repr}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit log entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries cannot be deleted.")
