import pyotp
from django.test import Client, TestCase

from accounts.models import AuditLog, CustomUser
from accounts.services import (
    confirm_2fa_setup,
    disable_2fa,
    generate_2fa_secret,
    get_provisioning_uri,
    verify_2fa_code,
)


class TwoFactorAuthTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="security.officer", email="sec@example.com", password="testpass123",
            role=CustomUser.Role.SECURITY,
        )

    def test_generate_secret_does_not_enable_2fa_yet(self):
        generate_2fa_secret(self.user)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.two_fa_secret)
        self.assertFalse(self.user.is_2fa_enabled)

    def test_valid_code_confirms_setup_and_enables_2fa(self):
        secret = generate_2fa_secret(self.user)
        valid_code = pyotp.TOTP(secret).now()

        confirmed = confirm_2fa_setup(self.user, valid_code)

        self.assertTrue(confirmed)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_2fa_enabled)

    def test_invalid_code_does_not_enable_2fa(self):
        generate_2fa_secret(self.user)
        confirmed = confirm_2fa_setup(self.user, "000000")

        self.assertFalse(confirmed)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_2fa_enabled)

    def test_verify_rejects_wrong_code(self):
        secret = generate_2fa_secret(self.user)
        wrong_code = str((int(pyotp.TOTP(secret).now()) + 1) % 1000000).zfill(6)
        self.assertFalse(verify_2fa_code(self.user, wrong_code))

    def test_provisioning_uri_without_secret_raises(self):
        with self.assertRaises(ValueError):
            get_provisioning_uri(self.user)

    def test_disable_2fa_clears_secret(self):
        secret = generate_2fa_secret(self.user)
        confirm_2fa_setup(self.user, pyotp.TOTP(secret).now())

        disable_2fa(self.user)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_2fa_enabled)
        self.assertIsNone(self.user.two_fa_secret)


class AuditTrailSignalTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="hr.audit", email="hraudit@example.com", password="testpass123",
            role=CustomUser.Role.HR,
        )
        self.client = Client()

    def test_successful_login_creates_audit_log_entry(self):
        self.client.login(username="hr.audit", password="testpass123")
        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN, actor=self.user).first()
        self.assertIsNotNone(entry)

    def test_failed_login_creates_audit_log_entry_without_actor(self):
        self.client.login(username="hr.audit", password="wrong-password")
        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).first()
        self.assertIsNotNone(entry)
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.metadata.get("attempted_username"), "hr.audit")

    def test_logout_creates_audit_log_entry(self):
        self.client.login(username="hr.audit", password="testpass123")
        self.client.logout()
        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGOUT, actor=self.user).first()
        self.assertIsNotNone(entry)

    def test_audit_log_is_append_only_via_admin(self):
        from django.contrib.admin.sites import site

        model_admin = site._registry[AuditLog]
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))
