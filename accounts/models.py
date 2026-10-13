import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class CustomUser(AbstractUser):
    """
    Auth identity for anyone who logs into the system (Admin, HR, managers,
    department heads, security officers). Not every enrolled Employee needs
    one of these — see employees.Employee.user_account.
    """

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrator"
        HR = "HR", "HR"
        AREA_MANAGER = "AREA_MANAGER", "Unit/Area manager"
        DEPT_HEAD = "DEPT_HEAD", "Department head"
        SECURITY = "SECURITY", "Security officer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=20, choices=Role.choices, db_index=True)
    is_2fa_enabled = models.BooleanField(default=False)
    two_fa_secret = models.CharField(max_length=64, blank=True, null=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["role", "is_active"])]

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class AuditLog(models.Model):
    """Append-only. Application code should never update or delete rows here."""

    class Action(models.TextChoices):
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed login"
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        ATTENDANCE_CORRECTION = "ATT_CORRECTION", "Attendance correction"
        EXPORT = "EXPORT", "Data export"

    actor = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, related_name="audit_events"
    )
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    target_model = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["actor", "timestamp"]),
            models.Index(fields=["target_model", "target_id"]),
        ]
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.get_action_display()} by {self.actor} @ {self.timestamp:%Y-%m-%d %H:%M}"
