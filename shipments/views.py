import csv
from collections import Counter
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from accounts.decorators import permission_required
from accounts.models import Profile, get_profile

from . import services
from .forms import DeliveryConfirmationForm, DocumentUploadForm, ShipmentEventForm, ShipmentForm
from .models import (
    CLIENT_VISIBLE_DOC_TYPES,
    AuditLog,
    DeliveryConfirmation,
    Document,
    Notification,
    Shipment,
    ShipmentEvent,
    Status,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def paginate(request, queryset, per_page=15):
    page = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, params.urlencode()


def get_visible_shipment(request, pk, include_archived=True):
    return get_object_or_404(services.visible_shipments(request.user, include_archived=include_archived), pk=pk)


def flash_form_errors(request, form):
    for field, errors in form.errors.items():
        label = "" if field == "__all__" else f"{form.fields[field].label or field.replace('_', ' ').title()}: "
        for error in errors:
            messages.error(request, f"{label}{error}")


def shipment_state(user, shipment):
    """Cheap fingerprint of a record, polled by the browser for live updates."""
    return {
        "version": shipment.version,
        "status": shipment.status,
        "updated_at": shipment.updated_at.isoformat(),
        "events": shipment.events.count(),
        "documents": services.visible_documents(user, shipment).count(),
        "pod": shipment.has_pod,
    }


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------
@login_required
def dashboard(request):
    data = services.dashboard_data(request.user)
    ctx = {
        "data": data,
        "recent": services.visible_shipments(request.user)[:8],
        "recent_notifications": request.user.notifications.all()[:6],
    }
    return render(request, "shipments/dashboard.html", ctx)


@permission_required("can_run_checks")
@require_POST
def run_checks(request):
    created = services.run_health_checks()
    messages.success(request, f"Record health check complete: {created} new alert(s) raised.")
    return redirect("dashboard")


# ---------------------------------------------------------------------------
# shipment list / CRUD
# ---------------------------------------------------------------------------
@login_required
def shipment_list(request):
    profile = get_profile(request.user)
    show_archived = request.GET.get("archived") == "1" and profile.can_delete_shipments
    qs = services.visible_shipments(request.user, include_archived=show_archived)
    if show_archived:
        qs = qs.filter(is_archived=True)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(tracking_number__icontains=q) | Q(reference_number__icontains=q) | Q(sender_name__icontains=q)
            | Q(receiver_name__icontains=q) | Q(origin__icontains=q) | Q(destination__icontains=q)
        )
    status = request.GET.get("status", "")
    if status in Status.values:
        qs = qs.filter(status=status)
    issue = request.GET.get("issue", "")
    if issue in services.ISSUE_LABELS and profile.is_internal:
        qs = services.issue_querysets(qs)[issue]
    assigned = request.GET.get("assigned", "")
    if assigned == "me":
        qs = qs.filter(assigned_to=request.user)
    elif assigned.isdigit():
        qs = qs.filter(assigned_to_id=int(assigned))
    date_from, date_to = parse_date(request.GET.get("from", "")), parse_date(request.GET.get("to", ""))
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    page, query_string = paginate(request, qs)
    ctx = {
        "page": page,
        "query_string": query_string,
        "statuses": Status.choices,
        "issues": services.ISSUE_LABELS,
        "staff_users": User.objects.filter(
            is_active=True, profile__role__in=[Profile.Role.DISPATCH, Profile.Role.STAFF]
        ).order_by("username"),
        "filters": {"q": q, "status": status, "issue": issue, "assigned": assigned,
                    "from": request.GET.get("from", ""), "to": request.GET.get("to", ""),
                    "archived": "1" if show_archived else ""},
        "show_archived": show_archived,
    }
    return render(request, "shipments/list.html", ctx)


@permission_required("can_create_shipments")
def shipment_create(request):
    form = ShipmentForm(request.POST or None)
    duplicates = []
    if request.method == "POST" and form.is_valid():
        shipment = form.save(commit=False)
        duplicates = list(services.find_duplicates(shipment))
        if not duplicates or request.POST.get("confirm_duplicate"):
            shipment.created_by = request.user
            with transaction.atomic():
                services.save_with_version(shipment, request.user, "Record created")
                if duplicates:
                    services.flag_duplicate(shipment, duplicates[0], request.user)
                if shipment.assigned_to and shipment.assigned_to != request.user:
                    services.notify(
                        [shipment.assigned_to], Notification.Kind.ASSIGNED,
                        f"You were assigned shipment {shipment.tracking_number} ({shipment.destination}).",
                        link=services.shipment_link(shipment), shipment=shipment, email=True,
                    )
            if duplicates:
                messages.warning(request, f"{shipment.tracking_number} saved but flagged as a possible duplicate for manager review.")
            else:
                messages.success(request, f"Shipment {shipment.tracking_number} created.")
            return redirect(shipment)
    return render(request, "shipments/form.html", {"form": form, "duplicates": duplicates, "title": "Register new shipment"})


@login_required
def shipment_detail(request, pk):
    shipment = get_visible_shipment(request, pk)
    profile = get_profile(request.user)
    documents = list(services.visible_documents(request.user, shipment))
    ctx = {
        "shipment": shipment,
        "events": shipment.events.select_related("created_by"),
        "current_docs": [d for d in documents if d.is_current],
        "old_docs": [d for d in documents if not d.is_current],
        "version_count": shipment.versions.count(),
        "issues": services.shipment_issues(shipment) if profile.is_internal and not shipment.is_archived else [],
        "event_form": ShipmentEventForm(can_cancel=profile.can_edit_shipments),
        "upload_form": DocumentUploadForm(),
        "state": shipment_state(request.user, shipment),
        "poll_seconds": 8,
    }
    return render(request, "shipments/detail.html", ctx)


@permission_required("can_edit_shipments")
def shipment_edit(request, pk):
    shipment = get_visible_shipment(request, pk, include_archived=False)
    old_assignee = shipment.assigned_to_id
    if request.method == "POST":
        form = ShipmentForm(request.POST, instance=shipment)
        if form.is_valid():
            try:
                posted_version = int(request.POST.get("version") or 0)
            except ValueError:
                posted_version = 0
            with transaction.atomic():
                current = Shipment.objects.select_for_update().values_list("version", flat=True).get(pk=pk)
                if posted_version != current:
                    # Optimistic locking: someone else changed the record meanwhile.
                    form.add_error(
                        None,
                        "This record was changed by someone else while you were editing "
                        f"(you had v{posted_version}, the latest is v{current}). "
                        "Reload the latest version and re-apply your changes.",
                    )
                elif not [f for f in form.changed_data if f != "version"]:
                    messages.info(request, "No changes were made.")
                    return redirect(shipment)
                else:
                    updated = form.save(commit=False)
                    services.save_with_version(updated, request.user, "Record edited")
                    match = services.find_duplicates(updated).first()
                    if match and not updated.is_flagged_duplicate and not updated.duplicate_reviewed:
                        services.flag_duplicate(updated, match, request.user)
                        messages.warning(request, "These details now match another shipment and were flagged for review.")
                    if updated.assigned_to_id and updated.assigned_to_id != old_assignee:
                        services.notify(
                            [updated.assigned_to], Notification.Kind.ASSIGNED,
                            f"You were assigned shipment {updated.tracking_number} ({updated.destination}).",
                            link=services.shipment_link(updated), shipment=updated, email=True,
                        )
                    messages.success(request, f"Shipment {updated.tracking_number} updated (now version {updated.version}).")
                    return redirect(updated)
    else:
        form = ShipmentForm(instance=shipment, initial={"version": shipment.version})
    return render(request, "shipments/form.html", {"form": form, "shipment": shipment, "title": f"Edit {shipment.tracking_number}"})


@permission_required("can_delete_shipments")
@require_POST
def shipment_archive(request, pk):
    shipment = get_visible_shipment(request, pk, include_archived=False)
    services.archive_shipment(shipment, request.user, request.POST.get("reason", "").strip())
    messages.success(request, f"{shipment.tracking_number} archived. It can be restored from the archive.")
    return redirect("shipment_list")


@permission_required("can_delete_shipments")
@require_POST
def shipment_restore(request, pk):
    shipment = get_object_or_404(services.visible_shipments(request.user, include_archived=True), pk=pk, is_archived=True)
    services.unarchive_shipment(shipment, request.user)
    messages.success(request, f"{shipment.tracking_number} restored.")
    return redirect(shipment)


# ---------------------------------------------------------------------------
# tracking events, documents, versions
# ---------------------------------------------------------------------------
@permission_required("can_add_updates")
@require_POST
def event_add(request, pk):
    shipment = get_visible_shipment(request, pk, include_archived=False)
    profile = get_profile(request.user)
    form = ShipmentEventForm(request.POST, can_cancel=profile.can_edit_shipments)
    if shipment.is_final:
        messages.error(request, "This shipment is already closed; no further updates can be added.")
    elif form.is_valid():
        services.add_event(
            shipment, form.cleaned_data["status"], request.user,
            location=form.cleaned_data["location"], note=form.cleaned_data["note"],
        )
        messages.success(request, "Status update recorded.")
    else:
        flash_form_errors(request, form)
    return redirect(shipment)


@permission_required("can_upload_documents")
@require_POST
def document_upload(request, pk):
    shipment = get_visible_shipment(request, pk, include_archived=False)
    form = DocumentUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        flash_form_errors(request, form)
        return redirect(shipment)
    try:
        doc, elsewhere = services.upload_document(
            shipment, form.cleaned_data["file"], form.cleaned_data["doc_type"], form.cleaned_data["title"], request.user
        )
    except services.DuplicateDocument as exc:
        messages.warning(
            request,
            f"Duplicate file rejected: identical to '{exc.existing.original_name}' "
            f"(v{exc.existing.version}) already on this shipment.",
        )
    else:
        msg = f"{doc.display_title} uploaded as version {doc.version}."
        if doc.version > 1:
            msg += " The previous version is now marked outdated."
        messages.success(request, msg)
        if elsewhere:
            messages.info(request, f"Note: the same file is also attached to {elsewhere.tracking_number}.")
    return redirect(shipment)


@login_required
def document_download(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    get_visible_shipment(request, doc.shipment_id)  # 404 if the user cannot see the shipment
    if get_profile(request.user).role == Profile.Role.CLIENT and doc.doc_type not in CLIENT_VISIBLE_DOC_TYPES:
        raise Http404
    if not doc.file or not doc.file.storage.exists(doc.file.name):
        raise Http404("File is missing from storage.")
    return FileResponse(doc.file.open("rb"), as_attachment=request.GET.get("download") == "1", filename=doc.original_name)


@permission_required("can_upload_documents")
@require_POST
def document_current(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    get_visible_shipment(request, doc.shipment_id, include_archived=False)
    services.make_document_current(doc, request.user)
    messages.success(request, f"{doc.display_title} version {doc.version} is now the current version.")
    return redirect(doc.shipment)


@login_required
def shipment_versions(request, pk):
    shipment = get_visible_shipment(request, pk)
    if not get_profile(request.user).is_internal:
        raise Http404
    rows = []
    for version in shipment.versions.select_related("changed_by"):
        changes = [
            (services.FIELD_LABELS.get(f, f), services.describe_value(f, old), services.describe_value(f, new))
            for f, (old, new) in version.changes.items()
        ]
        rows.append({"version": version, "changes": changes})
    return render(request, "shipments/versions.html", {"shipment": shipment, "rows": rows})


@permission_required("can_edit_shipments")
@require_POST
def version_restore(request, pk, number):
    shipment = get_visible_shipment(request, pk, include_archived=False)
    get_object_or_404(shipment.versions, version_number=number)
    services.restore_version(shipment, number, request.user)
    messages.success(request, f"Record restored from version {number} (saved as new version {shipment.version}).")
    return redirect(shipment)


# ---------------------------------------------------------------------------
# delivery confirmation (proof of delivery)
# ---------------------------------------------------------------------------
@permission_required("can_confirm_delivery")
def delivery_confirm(request, pk):
    shipment = get_visible_shipment(request, pk, include_archived=False)
    if shipment.status in (Status.DELIVERED, Status.CANCELLED) or shipment.has_pod:
        messages.info(request, "Delivery confirmation is not available for this shipment (already delivered or cancelled).")
        return redirect(shipment)
    form = DeliveryConfirmationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.confirm_delivery(
                shipment, request.user, received_by=data["received_by"],
                receiver_id_number=data["receiver_id_number"], relationship=data["relationship"],
                notes=data["notes"], delivered_at=data["delivered_at"], signature=data["signature_data"],
                photo=data["photo"], latitude=data["latitude"], longitude=data["longitude"],
            )
        except (services.AlreadyDelivered, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(shipment)
        messages.success(request, "Delivery confirmed and proof of delivery stored.")
        return redirect("pod_view", pk=shipment.pk)
    return render(request, "shipments/delivery_form.html", {"form": form, "shipment": shipment})


@login_required
def pod_view(request, pk):
    shipment = get_visible_shipment(request, pk)
    pod = get_object_or_404(DeliveryConfirmation.objects.select_related("confirmed_by"), shipment=shipment)
    return render(request, "shipments/pod.html", {"shipment": shipment, "pod": pod})


@login_required
def pod_file(request, pk, kind):
    """Serve the signature / photo through an access check (never via a public URL)."""
    if kind not in ("signature", "photo"):
        raise Http404
    pod = get_object_or_404(DeliveryConfirmation, pk=pk)
    get_visible_shipment(request, pod.shipment_id)
    field = getattr(pod, kind)
    if not field or not field.storage.exists(field.name):
        raise Http404
    return FileResponse(field.open("rb"))


# ---------------------------------------------------------------------------
# duplicate review
# ---------------------------------------------------------------------------
@permission_required("can_review_duplicates")
def duplicates(request):
    flagged = Shipment.objects.filter(is_archived=False, is_flagged_duplicate=True).select_related(
        "duplicate_of", "created_by"
    ).order_by("created_at")
    return render(request, "shipments/duplicates.html", {"flagged": flagged})


@permission_required("can_review_duplicates")
@require_POST
def duplicate_resolve(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk, is_archived=False, is_flagged_duplicate=True)
    action = request.POST.get("action")
    if action not in ("archive", "not_duplicate"):
        raise Http404
    services.resolve_duplicate(shipment, request.user, action)
    messages.success(
        request,
        f"{shipment.tracking_number} archived as a duplicate." if action == "archive"
        else f"{shipment.tracking_number} confirmed as a separate shipment.",
    )
    return redirect("duplicates")


@permission_required("can_review_duplicates")
@require_POST
def scan_duplicates(request):
    count = services.scan_duplicates()
    messages.success(request, f"Duplicate scan complete: {count} new record(s) flagged.")
    return redirect("duplicates")


# ---------------------------------------------------------------------------
# notifications
# ---------------------------------------------------------------------------
@login_required
def notifications(request):
    page, query_string = paginate(request, request.user.notifications.select_related("shipment"), per_page=20)
    return render(request, "shipments/notifications.html", {"page": page, "query_string": query_string})


@login_required
def notification_open(request, pk):
    note = get_object_or_404(Notification, pk=pk, recipient=request.user)
    note.is_read = True
    note.save(update_fields=["is_read"])
    if note.link.startswith("/") and not note.link.startswith("//"):
        return redirect(note.link)
    return redirect("notifications")


@login_required
@require_POST
def notifications_read_all(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect("notifications")


# ---------------------------------------------------------------------------
# audit trail
# ---------------------------------------------------------------------------
@permission_required("can_view_audit")
def audit_log(request):
    qs = AuditLog.objects.all()
    user_q = request.GET.get("user", "").strip()
    if user_q:
        qs = qs.filter(username__icontains=user_q)
    action = request.GET.get("action", "")
    if action in AuditLog.Action.values:
        qs = qs.filter(action=action)
    text = request.GET.get("q", "").strip()
    if text:
        qs = qs.filter(Q(object_repr__icontains=text) | Q(model_name__icontains=text))
    d_from, d_to = parse_date(request.GET.get("from", "")), parse_date(request.GET.get("to", ""))
    if d_from:
        qs = qs.filter(timestamp__date__gte=d_from)
    if d_to:
        qs = qs.filter(timestamp__date__lte=d_to)
    page, query_string = paginate(request, qs, per_page=30)
    return render(request, "shipments/audit.html", {
        "page": page, "query_string": query_string, "actions": AuditLog.Action.choices,
        "filters": {"user": user_q, "action": action, "q": text,
                    "from": request.GET.get("from", ""), "to": request.GET.get("to", "")},
        "labels": services.FIELD_LABELS,
    })


# ---------------------------------------------------------------------------
# management reports
# ---------------------------------------------------------------------------
def _report_data(request):
    today = timezone.localdate()
    d_from = parse_date(request.GET.get("from", "")) or today - timedelta(days=29)
    d_to = parse_date(request.GET.get("to", "")) or today
    status = request.GET.get("status", "")
    base = services.visible_shipments(request.user, include_archived=True)
    qs = base.filter(created_at__date__gte=d_from, created_at__date__lte=d_to)
    if status in Status.values:
        qs = qs.filter(status=status)
    return d_from, d_to, status, qs


@permission_required("can_view_reports")
def reports(request):
    d_from, d_to, status, qs = _report_data(request)
    counts = {r["status"]: r["c"] for r in qs.order_by().values("status").annotate(c=Count("id"))}
    total = sum(counts.values())
    delivered = qs.filter(status=Status.DELIVERED)
    delivered_count = delivered.count()
    confirmations = list(
        DeliveryConfirmation.objects.filter(shipment__in=qs).select_related("shipment")
    )
    avg_hours = (
        sum((c.delivered_at - c.shipment.created_at).total_seconds() for c in confirmations) / len(confirmations) / 3600
        if confirmations else None
    )
    docs = Document.objects.filter(shipment__in=qs)
    issue_counts = {
        key: {"label": services.ISSUE_LABELS[key], "count": issue_qs.count()}
        for key, issue_qs in services.issue_querysets(qs).items()
    }
    destinations = list(
        qs.order_by().values("destination").annotate(c=Count("id")).order_by("-c")[:5]
    )
    ctx = {
        "filters": {"from": d_from.isoformat(), "to": d_to.isoformat(), "status": status},
        "statuses": Status.choices,
        "total": total,
        "status_rows": [(label, counts.get(value, 0), value) for value, label in Status.choices],
        "delivered_count": delivered_count,
        "pod_rate": round(100 * len(confirmations) / delivered_count) if delivered_count else 100,
        "avg_hours": round(avg_hours, 1) if avg_hours is not None else None,
        "archived_count": qs.filter(is_archived=True).count(),
        "reviewed_duplicates": qs.filter(duplicate_reviewed=True).count(),
        "docs_total": docs.count(),
        "docs_outdated": docs.filter(is_current=False).count(),
        "issue_counts": issue_counts,
        "destinations": destinations,
        "audit_events": AuditLog.objects.filter(timestamp__date__gte=d_from, timestamp__date__lte=d_to).count(),
        "chart": {"labels": [l for _, l in Status.choices], "values": [counts.get(v, 0) for v, _ in Status.choices]},
        "shipments": qs.select_related("assigned_to")[:50],
        "generated": timezone.localtime(),
    }
    return render(request, "shipments/reports.html", ctx)


def _csv_safe(value):
    """Prevent spreadsheet formula injection."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@permission_required("can_view_reports")
def report_csv(request):
    d_from, d_to, status, qs = _report_data(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="shipments_{d_from}_{d_to}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Tracking number", "Reference", "Sender", "Receiver", "Origin", "Destination", "Status", "Weight (kg)",
        "Packages", "Expected delivery", "Created", "Delivered at", "Received by", "Record version",
        "Flagged duplicate", "Archived",
    ])
    for s in qs.select_related("delivery_confirmation"):
        pod = getattr(s, "delivery_confirmation", None)
        writer.writerow([_csv_safe(v) for v in [
            s.tracking_number, s.reference_number, s.sender_name, s.receiver_name, s.origin, s.destination,
            s.get_status_display(), s.weight_kg, s.packages, s.expected_delivery or "",
            timezone.localtime(s.created_at).strftime("%Y-%m-%d %H:%M"),
            timezone.localtime(pod.delivered_at).strftime("%Y-%m-%d %H:%M") if pod else "",
            pod.received_by if pod else "", s.version, "Yes" if s.is_flagged_duplicate else "No",
            "Yes" if s.is_archived else "No",
        ]])
    return response


# ---------------------------------------------------------------------------
# public tracking (no login) - restores client confidence
# ---------------------------------------------------------------------------
def track(request):
    number = request.GET.get("number", "").strip().upper()
    if number:
        return redirect("track_result", number=number)
    return render(request, "shipments/track.html")


def track_result(request, number):
    shipment = Shipment.objects.filter(tracking_number=number.upper(), is_archived=False).first()
    if not shipment:
        return render(request, "shipments/track.html", {"not_found": number}, status=404)
    return render(request, "shipments/track_result.html", {
        "shipment": shipment,
        "events": shipment.events.all(),
        "pod": getattr(shipment, "delivery_confirmation", None),
    })


# ---------------------------------------------------------------------------
# JSON endpoints polled by the browser (near real-time updates)
# ---------------------------------------------------------------------------
@login_required
def api_dashboard(request):
    return JsonResponse(services.dashboard_data(request.user))


@login_required
def api_notifications(request):
    items = request.user.notifications.all()[:8]
    return JsonResponse({
        "unread": request.user.notifications.filter(is_read=False).count(),
        "items": [
            {
                "id": n.pk, "message": n.message, "kind": n.kind, "is_read": n.is_read,
                "created": timezone.localtime(n.created_at).strftime("%d %b %H:%M"),
                "url": reverse("notification_open", args=[n.pk]),
            }
            for n in items
        ],
    })


@login_required
def api_shipment_state(request, pk):
    return JsonResponse(shipment_state(request.user, get_visible_shipment(request, pk)))


@login_required
def api_pulse(request):
    """Lightweight change detector for the shipment list page."""
    qs = services.visible_shipments(request.user)
    latest = qs.order_by("-updated_at").values_list("updated_at", flat=True).first()
    return JsonResponse({"count": qs.count(), "latest": latest.isoformat() if latest else None})
