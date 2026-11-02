"""
T-Music Authentication Service
Business logic for user login and brute-force protection.

Hardening (2026-09 security audit):
- All auth events are logged to the "tmusic.auth" logger (no passwords).
- Unknown usernames burn a dummy Argon2id verify so response time does not
  reveal whether an account exists (username enumeration via timing).
- Successful logins migrate unambiguous bcrypt/legacy hashes to Argon2id.
"""
import logging
from typing import Optional, Tuple
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    verify_password, hash_password,
    create_access_token,
    timing_equalizer_hash, password_needs_rehash, password_change_required,
)
from app.core.rate_limiter import limiter
from app.core.login_device import device_id_for, issue_device_token
from app.core.user_cache import get_attached_user, invalidate_user
from app.utils.time import utcnow
from app.models.auth import AppUser
from app.schemas.auth import TokenResponse

logger = logging.getLogger("tmusic.auth")

# One answer for every account-level failure (H-1: no account enumeration).
GENERIC_LOGIN_ERROR = "Invalid credentials."


class AuthService:
    """Authentication and session management service."""

    @staticmethod
    def _ip_gate(client_ip: str, username: str = "-") -> Optional[str]:
        """IP-level lockout check. Returns a generic error message or None."""
        locked, _remaining = limiter.is_locked_out(client_ip)
        if locked:
            logger.warning("Login blocked (IP lockout): ip=%s user=%r", client_ip, username)
            return "Too many failed attempts. Try again later."
        return None

    @staticmethod
    def _account_locked(user: AppUser) -> bool:
        return bool(user.locked_until and user.locked_until > utcnow())

    @staticmethod
    def _burn_verify(password: str) -> None:
        """Spend one Argon2id verify so every failure branch costs the same."""
        verify_password(password, timing_equalizer_hash())

    @staticmethod
    def _register_failure(db: Session, user: AppUser, client_ip: str, what: str) -> None:
        """Record a failed attempt on both IP and account counters."""
        limiter.record_failure(
            client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = utcnow() + timedelta(minutes=settings.LOCKOUT_MINUTES)
            logger.warning(
                "Account locked for untrusted devices after %d failed attempts: ip=%s user=%r",
                user.failed_login_attempts, client_ip, user.username,
            )
        db.commit()
        invalidate_user(user.username)
        logger.warning(
            "Failed login (%s): ip=%s user=%r attempts=%d",
            what, client_ip, user.username, user.failed_login_attempts,
        )

    @staticmethod
    def _finish_success(db: Session, user: AppUser, client_ip: str,
                        claims: dict, kind: str,
                        device_id: Optional[str] = None) -> Tuple[TokenResponse, None]:
        """Reset counters and mint a session token (shared tail of all flows)."""
        limiter.record_success(client_ip)
        if device_id:
            limiter.record_success(f"device:{device_id}")
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()
        invalidate_user(user.username)
        logger.info("Successful login (%s): ip=%s user=%r", kind, client_ip, user.username)
        token_str, _, expire_dt = create_access_token(
            subject=user.username, claims=claims,
            expires_delta=timedelta(minutes=10) if claims.get("pwd_reset") else None,
        )
        return TokenResponse(
            access_token=token_str,
            token_type="bearer",
            expires_in=int((expire_dt - utcnow()).total_seconds()),
            password_change_required=claims.get("pwd_reset", False),
            device_token=issue_device_token(user.id, user.token_version or 0),
        ), None

    @staticmethod
    def authenticate_by_password(
        db: Session,
        username: str,
        password: str,
        client_ip: str,
        device_token: Optional[str] = None,
    ) -> Tuple[Optional[TokenResponse], Optional[str]]:
        """Authenticate with brute-force protection that cannot lock out the owner.

        2026-11-02 audit (H-1):
        * A valid known-device cookie bypasses the account-wide and per-IP
          lockouts; its failures are counted (and locked) per device only.
        * Untrusted clients keep the per-IP and per-account limits.
        * Every account-level failure (unknown, inactive, locked, wrong
          password) answers with the same message after one Argon2id verify,
          so neither the text nor the timing reveals whether an account exists.
        """
        user = db.execute(
            select(AppUser).where(AppUser.username == username).with_for_update()
        ).scalar_one_or_none()
        usable = bool(user and user.is_active)
        device_id = (
            device_id_for(device_token, user.id, user.token_version or 0) if usable else None
        )

        if device_id:
            device_key = f"device:{device_id}"
            locked, _ = limiter.is_locked_out(device_key)
            if locked:
                AuthService._burn_verify(password)
                logger.warning("Login blocked (device lockout): ip=%s user=%r", client_ip, username)
                return None, GENERIC_LOGIN_ERROR
            if not verify_password(password, user.password_hash):
                limiter.record_failure(
                    device_key, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
                logger.warning("Failed login (bad password, known device): ip=%s user=%r", client_ip, username)
                return None, GENERIC_LOGIN_ERROR
        else:
            # 1. IP lockout (independent of the username, so it leaks nothing)
            if err := AuthService._ip_gate(client_ip, username):
                return None, err
            # 2. Unknown / inactive account
            if not usable:
                AuthService._burn_verify(password)
                limiter.record_failure(client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
                logger.warning("Failed login (unknown/inactive user): ip=%s user=%r", client_ip, username)
                return None, GENERIC_LOGIN_ERROR
            # 3. Account locked for untrusted devices: same cost, same answer
            if AuthService._account_locked(user):
                AuthService._burn_verify(password)
                limiter.record_failure(client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
                logger.warning("Login blocked (account lockout, untrusted device): ip=%s user=%r", client_ip, username)
                return None, GENERIC_LOGIN_ERROR
            # 4. Verify password
            if not verify_password(password, user.password_hash):
                AuthService._register_failure(db, user, client_ip, "bad password")
                return None, GENERIC_LOGIN_ERROR

        # Rehash only an unambiguous password. Row lock prevents a concurrent
        # password reset from being overwritten by opportunistic migration.
        must_change = password_change_required(password, user.password_hash)
        if not must_change and password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            logger.info("Upgraded password hash to Argon2id: user=%r", username)
        claims = {"uid": user.id, "tv": user.token_version or 0}
        if must_change:
            claims["pwd_reset"] = True
        return AuthService._finish_success(
            db, user, client_ip, claims, "password" if not device_id else "password, known device",
            device_id=device_id,
        )

    @staticmethod
    def change_password(db: Session, user: AppUser, new_password: str):
        """Set a new Argon2id password and revoke every previously issued token."""
        target = get_attached_user(db, user)
        if target is None:
            raise ValueError("User not found.")
        target.password_hash = hash_password(new_password)
        target.failed_login_attempts = 0
        target.locked_until = None
        target.token_version = (target.token_version or 0) + 1
        target.updated_at = utcnow()
        db.commit()
        invalidate_user(target.username)
        logger.info("Password changed, sessions revoked: user=%r", target.username)


    @staticmethod
    def change_password_with_current(db: Session, user: AppUser, current_password: str, new_password: str) -> bool:
        target = db.execute(select(AppUser).where(AppUser.id == user.id).with_for_update()).scalar_one_or_none()
        if target is None or not target.is_active or not verify_password(current_password, target.password_hash):
            return False
        AuthService.change_password(db, target, new_password)
        return True
