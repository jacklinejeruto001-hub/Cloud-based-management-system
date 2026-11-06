from django.contrib import admin

from .models import AuditLog, DeliveryConfirmation, Document, Notification, Shipment, ShipmentEvent, ShipmentVersion


class EventInline(admin.TabularInline):
    model = ShipmentEvent
    extra = 0


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("tracking_number", "sender_name", "receiver_name", "destination", "status", "version", "is_archived")
    list_filter = ("status", "is_archived", "is_flagged_duplicate")
    search_fields = ("tracking_number", "reference_number", "sender_name", "receiver_name")
    readonly_fields = ("tracking_number", "fingerprint", "version", "created_at", "updated_at")
    inlines = [EventInline]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("shipment", "doc_type", "version", "is_current", "original_name", "uploaded_at")
    list_filter = ("doc_type", "is_current")


admin.site.register(DeliveryConfirmation)
admin.site.register(ShipmentVersion)
admin.site.register(Notification)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: the audit trail must never be edited from the admin site."""

    list_display = ("timestamp", "username", "action", "model_name", "object_repr", "ip_address")
    list_filter = ("action", "model_name")
    search_fields = ("username", "object_repr")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
