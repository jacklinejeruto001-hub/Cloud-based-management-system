from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class Profile(models.Model):
    """Extends Django's User with a role used for role-based access control."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrator"
        MANAGER = "MANAGER", "Manager"
        STAFF = "STAFF", "Operations Staff"
        DISPATCH = "DISPATCH", "Dispatcher / Driver"
        CLIENT = "CLIENT", "Client"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)
    phone = models.CharField(max_length=30, blank=True)
    company = models.CharField(max_length=120, blank=True, help_text="For client accounts.")

    def __str__(self):
        return f"{self.user} ({self.get_role_display()})"

    # ---- permission matrix (single source of truth for RBAC) --------------
    @property
    def is_internal(self):
        return self.role != self.Role.CLIENT

    @property
    def can_create_shipments(self):
        return self.role in (self.Role.ADMIN, self.Role.MANAGER, self.Role.STAFF)

    can_edit_shipments = can_create_shipments
    can_upload_documents = can_create_shipments

    @property
    def can_delete_shipments(self):
        return self.role in (self.Role.ADMIN, self.Role.MANAGER)

    can_review_duplicates = can_delete_shipments
    can_view_reports = can_delete_shipments
    can_view_audit = can_delete_shipments
    can_run_checks = can_delete_shipments

    @property
    def can_add_updates(self):
        return self.role in (self.Role.ADMIN, self.Role.MANAGER, self.Role.STAFF, self.Role.DISPATCH)

    can_confirm_delivery = can_add_updates

    @property
    def can_manage_users(self):
        return self.role == self.Role.ADMIN


def get_profile(user):
    """Return the user's profile, creating a default one if it is missing."""
    try:
        return user.profile
    except ObjectDoesNotExist:
        role = Profile.Role.ADMIN if user.is_superuser else Profile.Role.STAFF
        return Profile.objects.create(user=user, role=role)
