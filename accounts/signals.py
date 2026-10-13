from django.contrib.auth.models import Group
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import AuditLog, CustomUser


@receiver(post_save, sender=CustomUser)
def sync_role_group(sender, instance, **kwargs):
    """
    Keeps group membership consistent with the `role` field so permission
    checks work whether code checks `user.role == "HR"` or
    `user.groups.filter(name="HR")` / `user.has_perm(...)`.
    """
    if not instance.role:
        return
    group, _ = Group.objects.get_or_create(name=instance.role)
    instance.groups.set([group])
    if instance.role == CustomUser.Role.ADMIN and not instance.is_superuser:
        CustomUser.objects.filter(pk=instance.pk).update(is_superuser=True, is_staff=True)


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    AuditLog.objects.create(
        actor=user, action=AuditLog.Action.LOGIN, ip_address=_client_ip(request),
    )
    ip = _client_ip(request)
    if ip:
        CustomUser.objects.filter(pk=user.pk).update(last_login_ip=ip)


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    if user is None:  # can be None if the session was already invalid
        return
    AuditLog.objects.create(
        actor=user, action=AuditLog.Action.LOGOUT, ip_address=_client_ip(request),
    )


@receiver(user_login_failed)
def log_login_failed(sender, credentials, request=None, **kwargs):
    AuditLog.objects.create(
        actor=None,
        action=AuditLog.Action.LOGIN_FAILED,
        ip_address=_client_ip(request),
        metadata={"attempted_username": credentials.get("username", "")},
    )
