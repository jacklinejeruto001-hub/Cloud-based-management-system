"""
Business logic for CBSRMS. Views stay thin; everything that enforces the data
quality rules from the project proposal lives here:

  * automated duplicate detection            -> find_duplicates / scan_duplicates
  * version control of shipment records      -> save_with_version / restore_version
  * document versioning + file deduplication -> upload_document
  * automated delivery confirmation (POD)    -> confirm_delivery
  * notifications and alerts                 -> notify / run_health_checks
  * audit trail                              -> write_audit
  * management-dashboard figures             -> dashboard_data
"""
import difflib
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, get_profile

from .middleware import get_current_ip, get_current_user
from .models import (
    CLIENT_VISIBLE_DOC_TYPES,
    FINAL_STATUSES,
    AuditLog,
    DeliveryConfirmation,
    Document,
    DocType,
    Notification,
    Shipment,
    ShipmentEvent,
    ShipmentVersion,
    Status,
)
from .utils import compute_fingerprint, normalize, sha256_of_file

User = get_user_model()


# ===========================================================================
# Audit trail
# ===========================================================================
def write_audit(action, obj=None, changes=None, user=None, model_name="", object_repr="", object_id=""):
    user = user or get_current_user()
    AuditLog.objects.create(
        user=user,
        username=user.get_username() if user else "system",
        action=action,
        model_name=model_name or (obj.__class__.__name__ if obj is not None else ""),
        object_id=str(object_id or (obj.pk if obj is not None else "")),
        object_repr=(object_repr or (str(obj) if obj is not None else ""))[:200],
        changes=changes or {},
        ip_address=get_current_ip(),
    )


# ===========================================================================
# Access scoping
# ===========================================================================
def visible_shipments(user, include_archived=False):
    """Shipments this user may see (role-based row-level scoping)."""
    profile = get_profile(user)
    qs = Shipment.objects.select_related("client", "assigned_to", "created_by")
    if not (include_archived and profile.can_delete_shipments):
        qs = qs.filter(is_archived=False)
    if profile.role == Profile.Role.CLIENT:
        qs = qs.filter(client=user)
    elif profile.role == Profile.Role.DISPATCH:
        qs = qs.filter(assigned_to=user)
    return qs


def visible_documents(user, shipment):
    docs = shipment.documents.select_related("uploaded_by")
    if get_profile(user).role == Profile.Role.CLIENT:
        docs = docs.filter(doc_type__in=CLIENT_VISIBLE_DOC_TYPES)
    return docs


# ===========================================================================
# Notifications
# ===========================================================================
def manager_users():
    return User.objects.filter(is_active=True, profile__role__in=[Profile.Role.ADMIN, Profile.Role.MANAGER])


def shipment_link(shipment):
    return reverse("shipment_detail", args=[shipment.pk])


def notify(users, kind, message, link="", shipment=None, dedupe_key="", email=False, dedupe_hours=24):
    """Create in-app notifications (and optionally e-mail). Returns number created."""
    now = timezone.now()
    seen, created = set(), 0
    for user in users:
        if user is None or not user.is_active or user.pk in seen:
            continue
        seen.add(user.pk)
        if dedupe_key and Notification.objects.filter(
            recipient=user, dedupe_key=dedupe_key, created_at__gte=now - timedelta(hours=dedupe_hours)
        ).exists():
            continue
        Notification.objects.create(
            recipient=user, kind=kind, message=message[:300], link=link, shipment=shipment, dedupe_key=dedupe_key
        )
        created += 1
        if email and user.email:
            send_mail(f"[CBSRMS] {message[:80]}", message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)
    return created


# ===========================================================================
# Duplicate detection
# ===========================================================================
def fingerprint_for(shipment):
    return compute_fingerprint(
        shipment.sender_name, shipment.receiver_name, shipment.origin, shipment.destination,
        shipment.description, shipment.weight_kg, shipment.packages,
    )


def find_duplicates(shipment):
    """Active shipments that look like the same consignment as `shipment`."""
    query = Q(fingerprint=fingerprint_for(shipment))
    if shipment.reference_number:
        query |= Q(reference_number__iexact=shipment.reference_number, sender_name__iexact=shipment.sender_name)
    qs = Shipment.objects.filter(is_archived=False).filter(query)
    if shipment.pk:
        qs = qs.exclude(pk=shipment.pk)
    return qs.order_by("created_at")


def flag_duplicate(shipment, original, user=None):
    shipment.is_flagged_duplicate = True
    shipment.duplicate_reviewed = False
    shipment.duplicate_of = original
    shipment.save(update_fields=["is_flagged_duplicate", "duplicate_reviewed", "duplicate_of", "fingerprint"])
    notify(
        manager_users(), Notification.Kind.DUPLICATE,
        f"{shipment.tracking_number} may duplicate {original.tracking_number} - please review.",
        link=reverse("duplicates"), shipment=shipment, dedupe_key=f"dup:{shipment.pk}",
    )


def scan_duplicates():
    """
    Batch scan used by `manage.py scan_duplicates`: flags exact fingerprint
    matches and near-matches (same sender, receiver and destination with a
    >= 90 % similar description). Returns the number of newly flagged records.
    """
    flagged = 0
    groups = defaultdict(list)
    for s in Shipment.objects.filter(is_archived=False).order_by("created_at"):
        groups[(normalize(s.sender_name), normalize(s.receiver_name), normalize(s.destination))].append(s)
    for items in groups.values():
        for i, newer in enumerate(items):
            if newer.is_flagged_duplicate or newer.duplicate_reviewed:
                continue
            for older in items[:i]:
                same = newer.fingerprint == older.fingerprint
                similar = difflib.SequenceMatcher(
                    None, normalize(older.description), normalize(newer.description)
                ).ratio() >= 0.9 and older.weight_kg == newer.weight_kg
                if same or similar:
                    flag_duplicate(newer, older)
                    flagged += 1
                    break
    return flagged


@transaction.atomic
def resolve_duplicate(shipment, user, action):
    """Manager decision on a flagged record: 'not_duplicate' or 'archive'."""
    original = shipment.duplicate_of
    shipment.is_flagged_duplicate = False
    shipment.duplicate_reviewed = True
    if action == "archive":
        reason = f"Duplicate of {original.tracking_number}" if original else "Duplicate record"
        archive_shipment(shipment, user, reason)
    else:
        shipment.duplicate_of = None
        save_with_version(shipment, user, "Reviewed: not a duplicate")


# ===========================================================================
# Version control for shipment records
# ===========================================================================
VERSIONED_FIELDS = [
    "reference_number", "sender_name", "sender_phone", "receiver_name", "receiver_phone", "receiver_email",
    "origin", "destination", "description", "weight_kg", "packages", "status", "expected_delivery",
    "client_id", "assigned_to_id", "is_archived",
]
# Fields a manager can roll back to an earlier version (status is driven by events/POD).
RESTORABLE_FIELDS = [f for f in VERSIONED_FIELDS if f not in ("status", "is_archived")]

FIELD_LABELS = {
    "reference_number": "Reference", "sender_name": "Sender", "sender_phone": "Sender phone",
    "receiver_name": "Receiver", "receiver_phone": "Receiver phone", "receiver_email": "Receiver e-mail",
    "origin": "Origin", "destination": "Destination", "description": "Description",
    "weight_kg": "Weight (kg)", "packages": "Packages", "status": "Status",
    "expected_delivery": "Expected delivery", "client_id": "Client", "assigned_to_id": "Assigned to",
    "is_archived": "Archived",
}


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def snapshot(shipment):
    return {f: _jsonable(getattr(shipment, f)) for f in VERSIONED_FIELDS}


def diff_snapshots(old, new):
    return {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}


def record_version(shipment, user, summary=""):
    previous = shipment.versions.first()  # ordered newest first
    new = snapshot(shipment)
    changes = diff_snapshots(previous.data, new) if previous else {}
    ShipmentVersion.objects.create(
        shipment=shipment, version_number=shipment.version, data=new, changes=changes,
        summary=summary[:255], changed_by=user,
    )
    return changes


@transaction.atomic
def save_with_version(shipment, user, summary=""):
    """Save a shipment, bump its version number and store a snapshot."""
    if shipment.pk is not None:
        shipment.version += 1
    shipment.last_activity = timezone.now()
    shipment.save()
    return record_version(shipment, user, summary)


@transaction.atomic
def archive_shipment(shipment, user, reason=""):
    shipment.is_archived = True
    shipment.archived_at = timezone.now()
    shipment.archived_by = user
    save_with_version(shipment, user, f"Archived. {reason}".strip())


@transaction.atomic
def unarchive_shipment(shipment, user):
    shipment.is_archived = False
    shipment.archived_at = None
    shipment.archived_by = None
    save_with_version(shipment, user, "Restored from archive")


@transaction.atomic
def restore_version(shipment, version_number, user):
    """Roll the editable fields back to an earlier snapshot (as a *new* version)."""
    target = shipment.versions.get(version_number=version_number)
    for field in RESTORABLE_FIELDS:
        value = target.data.get(field)
        if field == "weight_kg" and value is not None:
            value = Decimal(value)
        elif field == "expected_delivery" and value:
            value = date.fromisoformat(value)
        setattr(shipment, field, value)
    save_with_version(shipment, user, f"Restored from version {version_number}")


def describe_value(field, value):
    """Human-readable value for the version history screen."""
    if value in (None, ""):
        return "-"
    if field == "status":
        return dict(Status.choices).get(value, value)
    if field in ("client_id", "assigned_to_id"):
        user = User.objects.filter(pk=value).first()
        return user.get_full_name() or user.get_username() if user else f"#{value}"
    if field == "is_archived":
        return "Yes" if value else "No"
    return str(value)


# ===========================================================================
# Tracking events
# ===========================================================================
@transaction.atomic
def add_event(shipment, status, user, location="", note=""):
    if shipment.is_final:
        raise ValueError("This shipment is already closed.")
    previous = shipment.status
    event = ShipmentEvent.objects.create(shipment=shipment, status=status, location=location, note=note, created_by=user)
    shipment.status = status
    save_with_version(shipment, user, f"Status: {Status(status).label}")
    if previous == status:  # same status, new location/note: signal sees no field change, log explicitly
        write_audit(AuditLog.Action.STATUS, shipment, {"status": [previous, status], "location": location, "note": note}, user=user)
    if status == Status.FAILED:
        notify(
            [*manager_users(), shipment.assigned_to, shipment.created_by], Notification.Kind.FAILED,
            f"Delivery failed for {shipment.tracking_number}. {note}".strip(),
            link=shipment_link(shipment), shipment=shipment, email=True,
        )
    return event


# ===========================================================================
# Documents: versioning + file-level deduplication
# ===========================================================================
class DuplicateDocument(Exception):
    def __init__(self, existing):
        self.existing = existing
        super().__init__("This exact file is already attached to the shipment.")


@transaction.atomic
def upload_document(shipment, uploaded_file, doc_type, title, user):
    """
    Attach a file. Identical content already on this shipment is rejected
    (deduplication). A new file of the same type/title becomes the next version
    and the previous one is marked superseded (outdated) but kept for history.
    Returns (document, warning_shipment) where warning_shipment is another
    shipment holding the same file (informational only).
    """
    file_hash = sha256_of_file(uploaded_file)
    existing = shipment.documents.filter(file_hash=file_hash).first()
    if existing:
        raise DuplicateDocument(existing)

    group = shipment.documents.filter(doc_type=doc_type)
    if doc_type == DocType.OTHER:
        group = group.filter(title__iexact=title)
    latest = group.order_by("-version").first()
    now = timezone.now()
    if latest:
        group.filter(is_current=True).update(is_current=False, superseded_at=now)

    doc = Document.objects.create(
        shipment=shipment, doc_type=doc_type, title=title, file=uploaded_file,
        original_name=uploaded_file.name[:255], file_hash=file_hash, size_bytes=uploaded_file.size,
        version=(latest.version + 1) if latest else 1, uploaded_by=user,
    )
    Shipment.objects.filter(pk=shipment.pk).update(last_activity=now, updated_at=now)
    elsewhere = Document.objects.filter(file_hash=file_hash).exclude(shipment=shipment).select_related("shipment").first()
    return doc, (elsewhere.shipment if elsewhere else None)


@transaction.atomic
def make_document_current(document, user):
    group = document.shipment.documents.filter(doc_type=document.doc_type)
    if document.doc_type == DocType.OTHER:
        group = group.filter(title__iexact=document.title)
    group.exclude(pk=document.pk).filter(is_current=True).update(is_current=False, superseded_at=timezone.now())
    document.is_current = True
    document.superseded_at = None
    document.save()


# ===========================================================================
# Automated delivery confirmation (proof of delivery)
# ===========================================================================
class AlreadyDelivered(Exception):
    pass


@transaction.atomic
def confirm_delivery(shipment, user, *, received_by, receiver_id_number="", relationship="", notes="",
                     delivered_at=None, signature=None, photo=None, latitude=None, longitude=None):
    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.status == Status.DELIVERED or hasattr(shipment, "delivery_confirmation"):
        raise AlreadyDelivered("Delivery has already been confirmed for this shipment.")
    if shipment.status == Status.CANCELLED:
        raise ValueError("A cancelled shipment cannot be delivered.")

    delivered_at = delivered_at or timezone.now()
    confirmation = DeliveryConfirmation.objects.create(
        shipment=shipment, received_by=received_by, receiver_id_number=receiver_id_number,
        relationship=relationship, notes=notes, delivered_at=delivered_at, confirmed_by=user,
        signature=signature or "", photo=photo or "", latitude=latitude, longitude=longitude,
    )
    ShipmentEvent.objects.create(
        shipment=shipment, status=Status.DELIVERED, location=shipment.destination,
        note=f"Received by {received_by}", created_by=user, created_at=delivered_at,
    )
    shipment.status = Status.DELIVERED
    save_with_version(shipment, user, "Delivery confirmed (proof of delivery captured)")

    message = f"{shipment.tracking_number} delivered to {shipment.destination} - received by {received_by}."
    notify(
        [*manager_users(), shipment.created_by, shipment.client], Notification.Kind.DELIVERED, message,
        link=shipment_link(shipment), shipment=shipment,
    )
    emails = {e for e in (shipment.receiver_email, getattr(shipment.client, "email", "")) if e}
    if emails:
        send_mail(
            f"Delivery confirmation {shipment.tracking_number}",
            f"{message}\nDelivered at: {timezone.localtime(delivered_at):%d %b %Y %H:%M}\n"
            f"Track: /track/{shipment.tracking_number}/",
            settings.DEFAULT_FROM_EMAIL, sorted(emails), fail_silently=True,
        )
    return confirmation


# ===========================================================================
# Health checks / alerts
# ===========================================================================
ISSUE_LABELS = {
    "duplicate": "Possible duplicate awaiting review",
    "stale": "No update recorded (stale)",
    "overdue": "Past expected delivery date",
    "awaiting_pod": "Out for delivery, awaiting confirmation",
    "no_waybill": "No current waybill attached",
    "delivered_no_pod": "Marked delivered without proof of delivery",
}


def issue_querysets(base):
    """Shipments in `base` that currently have each kind of record-quality problem."""
    cfg = settings.CBSRMS
    now, today = timezone.now(), timezone.localdate()
    active = base.filter(is_archived=False).exclude(status__in=FINAL_STATUSES)
    waybills = Document.objects.filter(doc_type=DocType.WAYBILL, is_current=True).values("shipment_id")
    return {
        "duplicate": base.filter(is_archived=False, is_flagged_duplicate=True),
        "stale": active.filter(last_activity__lt=now - timedelta(hours=cfg["STALE_UPDATE_HOURS"])),
        "overdue": active.filter(expected_delivery__lt=today),
        "awaiting_pod": active.filter(
            status=Status.OUT_FOR_DELIVERY, last_activity__lt=now - timedelta(hours=cfg["POD_PENDING_HOURS"])
        ),
        "no_waybill": active.filter(created_at__lt=now - timedelta(hours=cfg["WAYBILL_GRACE_HOURS"])).exclude(
            pk__in=waybills
        ),
        "delivered_no_pod": base.filter(
            is_archived=False, status=Status.DELIVERED, delivery_confirmation__isnull=True
        ),
    }


def shipment_issues(shipment):
    """List of (key, label) problems affecting one shipment (record health panel)."""
    base = Shipment.objects.filter(pk=shipment.pk)
    return [(k, ISSUE_LABELS[k]) for k, qs in issue_querysets(base).items() if qs.exists()]


_ALERT_KIND = {
    "duplicate": Notification.Kind.DUPLICATE, "stale": Notification.Kind.STALE,
    "overdue": Notification.Kind.OVERDUE, "awaiting_pod": Notification.Kind.AWAITING_POD,
    "no_waybill": Notification.Kind.NO_WAYBILL, "delivered_no_pod": Notification.Kind.AWAITING_POD,
}
_ALERT_TEXT = {
    "duplicate": "is flagged as a possible duplicate",
    "stale": "has had no update recorded for over {h} hours",
    "overdue": "is past its expected delivery date",
    "awaiting_pod": "is out for delivery but has no delivery confirmation yet",
    "no_waybill": "has no current waybill document",
    "delivered_no_pod": "is marked delivered but has no proof of delivery",
}


def run_health_checks():
    """
    Generate alerts for every record-quality problem. Safe to run repeatedly:
    the same alert is not re-sent to the same person within 24 hours.
    Used by `manage.py generate_alerts` and the dashboard 'Run checks' button.
    """
    created = 0
    managers = list(manager_users())
    for key, qs in issue_querysets(Shipment.objects.all()).items():
        for s in qs.select_related("assigned_to", "created_by"):
            text = _ALERT_TEXT[key].format(h=settings.CBSRMS["STALE_UPDATE_HOURS"])
            created += notify(
                [*managers, s.assigned_to, s.created_by], _ALERT_KIND[key], f"{s.tracking_number} {text}.",
                link=shipment_link(s), shipment=s, dedupe_key=f"{key}:{s.pk}",
            )
    return created


# ===========================================================================
# Dashboard / report figures
# ===========================================================================
def dashboard_data(user):
    profile = get_profile(user)
    qs = visible_shipments(user)
    today = timezone.localdate()

    counts = {r["status"]: r["c"] for r in qs.order_by().values("status").annotate(c=Count("id"))}
    start = today - timedelta(days=13)
    days = [start + timedelta(days=i) for i in range(14)]
    created = {
        r["d"]: r["c"]
        for r in qs.filter(created_at__date__gte=start).annotate(d=TruncDate("created_at"))
        .order_by().values("d").annotate(c=Count("id"))
    }
    delivered = {
        r["d"]: r["c"]
        for r in DeliveryConfirmation.objects.filter(shipment__in=qs, delivered_at__date__gte=start)
        .annotate(d=TruncDate("delivered_at")).order_by().values("d").annotate(c=Count("id"))
    }

    total = sum(counts.values())
    delivered_total = counts.get(Status.DELIVERED, 0)
    with_pod = DeliveryConfirmation.objects.filter(shipment__in=qs).count()
    data = {
        "cards": {
            "total": total,
            "active": total - delivered_total - counts.get(Status.CANCELLED, 0),
            "in_transit": counts.get(Status.IN_TRANSIT, 0) + counts.get(Status.PICKED_UP, 0),
            "out_for_delivery": counts.get(Status.OUT_FOR_DELIVERY, 0),
            "delivered": delivered_total,
            "failed": counts.get(Status.FAILED, 0),
            "pod_rate": round(100 * with_pod / delivered_total) if delivered_total else 100,
            "unread": user.notifications.filter(is_read=False).count(),
        },
        "status": {
            "labels": [label for value, label in Status.choices],
            "values": [counts.get(value, 0) for value, label in Status.choices],
        },
        "daily": {
            "labels": [d.strftime("%d %b") for d in days],
            "created": [created.get(d, 0) for d in days],
            "delivered": [delivered.get(d, 0) for d in days],
        },
    }
    if profile.role in (Profile.Role.ADMIN, Profile.Role.MANAGER, Profile.Role.STAFF):
        iq = issue_querysets(qs)
        affected = set()
        issues = {}
        for key, issue_qs in iq.items():
            ids = set(issue_qs.values_list("pk", flat=True))
            issues[key] = {"label": ISSUE_LABELS[key], "count": len(ids)}
            affected |= ids
        data["issues"] = issues
        data["cards"]["overdue"] = issues["overdue"]["count"]
        data["cards"]["health"] = 100 if not total else round(100 * (1 - len(affected) / total))
    return data
