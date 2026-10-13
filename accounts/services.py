"""
TOTP-based two-factor authentication. Uses pyotp (RFC 6238 compliant,
compatible with Google Authenticator / Authy / any standard TOTP app) —
deliberately not a custom rolled implementation.
"""

import pyotp

from accounts.models import CustomUser

ISSUER_NAME = "Attendance System"


def generate_2fa_secret(user: CustomUser) -> str:
    """
    Generates and stores a new TOTP secret for the user. Does NOT enable
    2FA yet — call confirm_2fa_setup() after the user proves they can
    generate a valid code, so a user can't get locked out by saving a
    secret they never actually loaded into their authenticator app.
    """
    secret = pyotp.random_base32()
    user.two_fa_secret = secret
    user.save(update_fields=["two_fa_secret"])
    return secret


def get_provisioning_uri(user: CustomUser) -> str:
    """otpauth:// URI to render as a QR code in the setup UI."""
    if not user.two_fa_secret:
        raise ValueError("No 2FA secret generated yet — call generate_2fa_secret first")
    totp = pyotp.TOTP(user.two_fa_secret)
    return totp.provisioning_uri(name=user.email or user.username, issuer_name=ISSUER_NAME)


def verify_2fa_code(user: CustomUser, code: str) -> bool:
    if not user.two_fa_secret:
        return False
    totp = pyotp.TOTP(user.two_fa_secret)
    return totp.verify(code, valid_window=1)  # allow 1 step (30s) of clock drift


def confirm_2fa_setup(user: CustomUser, code: str) -> bool:
    """
    Called once, right after generate_2fa_secret + the user has scanned the
    QR code: proves they actually captured the secret correctly before
    2FA becomes mandatory on their account.
    """
    if not verify_2fa_code(user, code):
        return False
    user.is_2fa_enabled = True
    user.save(update_fields=["is_2fa_enabled"])
    return True


def disable_2fa(user: CustomUser):
    user.is_2fa_enabled = False
    user.two_fa_secret = None
    user.save(update_fields=["is_2fa_enabled", "two_fa_secret"])
