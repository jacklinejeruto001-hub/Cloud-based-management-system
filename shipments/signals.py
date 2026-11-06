"""
Audit-trail signal handlers. Every create / update / delete of the core
records is written to the append-only AuditLog together with the acting user
and IP address (supplied by CurrentUserMiddleware).
"""
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .middleware import client_ip
from .models import AuditLog, DeliveryConfirmation, Document, Shipment, ShipmentEvent
from .services import FIELD_LABELS, VERSIONED_FIELDS, _jsonable, write_audit

A = AuditLog.Action


def _model_diff(old, new, fields):
    changes = {}
    for f in fields:
        before, after = _jsonable(getattr(old, f)), _jsonable(getattr(new, f))
        if before != after:
            changes[f] = [before, after]
    return changes


# --- Shipment ---------------------------------------------------------------
@receiver(pre_save, sender=Shipment)
def shipment_pre_save(sender, instance, **kwargs):
    instance._audit_changes = None
    if instance.pk:
        old = Shipment.objects.filter(pk=instance.pk).first()
        if old:
            fields = VERSIONED_FIELDS + ["is_flagged_duplicate", "duplicate_reviewed"]
            instance._audit_changes = _model_diff(old, instance, fields)


@receiver(post_save, sender=Shipment)
def shipment_post_save(sender, instance, created, **kwargs):
    if created:
        write_audit(A.CREATE, instance, {"destination": instance.destination, "receiver": instance.receiver_name})
        return
    changes = getattr(instance, "_audit_changes", None)
    if not changes:
        return
    if "is_archived" in changes:
        action = A.ARCHIVE if instance.is_archived else A.RESTORE
    elif set(changes) == {"status"}:
        action = A.STATUS
    else:
        action = A.UPDATE
    write_audit(action, instance, changes)


@receiver(post_delete, sender=Shipment)
def shipment_post_delete(sender, instance, **kwargs):
    write_audit(A.DELETE, instance)


# --- Documents / POD ----------------------------------------------------------
@receiver(post_save, sender=Document)
def document_post_save(sender, instance, created, **kwargs):
    if created:
        write_audit(
            A.UPLOAD, instance,
            {"shipment": instance.shipment.tracking_number, "file": instance.original_name, "version": instance.version},
        )


@receiver(post_save, sender=DeliveryConfirmation)
def confirmation_post_save(sender, instance, created, **kwargs):
    if created:
        write_audit(
            A.CONFIRM, instance, {"shipment": instance.shipment.tracking_number, "received_by": instance.received_by}
        )


# --- Authentication -----------------------------------------------------------
@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    write_audit(A.LOGIN, user=user, model_name="User", object_repr=user.get_username(), object_id=user.pk)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user:
        write_audit(A.LOGOUT, user=user, model_name="User", object_repr=user.get_username(), object_id=user.pk)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    from .models import AuditLog as Log

    Log.objects.create(
        username=str(credentials.get("username", ""))[:150], action=A.LOGIN_FAILED, model_name="User",
        ip_address=client_ip(request) if request else None,
    )
