from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import AuditLog, CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ("username", "email", "role", "is_active", "is_2fa_enabled", "last_login")
    list_filter = ("role", "is_active", "is_2fa_enabled")
    search_fields = ("username", "email")
    fieldsets = UserAdmin.fieldsets + (
        ("Role & Security", {"fields": ("role", "is_2fa_enabled", "last_login_ip")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Role", {"fields": ("role", "email")}),
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action", "target_model", "target_id", "ip_address")
    list_filter = ("action", "target_model")
    search_fields = ("target_id", "actor__username")
    readonly_fields = [f.name for f in AuditLog._meta.fields]  # append-only: no edits via admin

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
