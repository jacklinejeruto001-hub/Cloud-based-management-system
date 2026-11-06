from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Profile

User = get_user_model()


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False


class UserAdmin(DjangoUserAdmin):
    inlines = [ProfileInline]
    list_display = ("username", "email", "first_name", "last_name", "is_active", "get_role")

    @admin.display(description="Role")
    def get_role(self, obj):
        return obj.profile.get_role_display() if hasattr(obj, "profile") else "-"


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
