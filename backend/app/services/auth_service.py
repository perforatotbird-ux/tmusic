"""
T-Music Authentication Service
Business logic for user login, PIN verification, and brute-force protection.

Hardening (2026-09 security audit):
- All auth events are logged to the "tmusic.auth" logger (no passwords/PINs).
- Unknown usernames burn a dummy bcrypt verify so response time does not
  reveal whether an account exists (username enumeration via timing).
- Successful logins auto-upgrade legacy salted SHA-256 hashes to bcrypt.
- PIN flow now applies the same per-account lockout as the password flow.
"""
import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    verify_password, verify_pin, hash_password, hash_pin,
    create_access_token, is_legacy_hash, is_legacy_pin_hash,
    timing_equalizer_hash,
)
from app.core.rate_limiter import limiter
from app.core.user_cache import get_attached_user, invalidate_user
from app.utils.time import utcnow
from app.models.auth import AppUser
from app.schemas.auth import TokenResponse

logger = logging.getLogger("tmusic.auth")


class AuthService:
    """Authentication and session management service."""

    @staticmethod
    def authenticate_by_password(
        db: Session,
        username: str,
        password: str,
        client_ip: str
    ) -> Tuple[Optional[TokenResponse], Optional[str]]:
        """Authenticate user with username and password with brute-force lockout."""
        # 1. Check IP lockout (generic message — exact countdown stays server-side)
        locked, _remaining = limiter.is_locked_out(client_ip)
        if locked:
            logger.warning("Login blocked (IP lockout): ip=%s user=%r", client_ip, username)
            return None, "Too many failed attempts. Try again later."

        # 2. Find user
        user = db.execute(select(AppUser).where(AppUser.username == username)).scalar_one_or_none()
        if not user or not user.is_active:
            # Burn a dummy bcrypt verify so timing does not reveal account existence
            verify_password(password, timing_equalizer_hash())
            limiter.record_failure(client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
            logger.warning("Failed login (unknown/inactive user): ip=%s user=%r", client_ip, username)
            return None, "Invalid credentials."

        # 3. Check account lockout (generic message — countdown stays server-side)
        if user.locked_until and user.locked_until > utcnow():
            logger.warning("Login blocked (account lockout): ip=%s user=%r", client_ip, username)
            return None, "Account temporarily locked. Try again later."

        # 4. Verify password
        if not verify_password(password, user.password_hash):
            is_locked, left = limiter.record_failure(client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
                user.locked_until = utcnow() + timedelta(minutes=settings.LOCKOUT_MINUTES)
                logger.warning(
                    "Account locked after %d failed attempts: ip=%s user=%r",
                    user.failed_login_attempts, client_ip, username,
                )
            db.commit()
            invalidate_user(user.username)
            logger.warning(
                "Failed login (bad password): ip=%s user=%r attempts=%d",
                client_ip, username, user.failed_login_attempts,
            )
            return None, "Invalid credentials."

        # 5. Success — auto-upgrade legacy password hash to bcrypt
        if is_legacy_hash(user.password_hash):
            user.password_hash = hash_password(password)
            logger.info("Upgraded legacy password hash to bcrypt: user=%r", username)

        limiter.record_success(client_ip)
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()
        invalidate_user(user.username)

        logger.info("Successful login (password): ip=%s user=%r", client_ip, username)
        token_str, _, expire_dt = create_access_token(
            subject=user.username,
            claims={"uid": user.id, "tv": user.token_version or 0}
        )
        return TokenResponse(
            access_token=token_str,
            token_type="bearer",
            expires_in=int((expire_dt - utcnow()).total_seconds())
        ), None

    @staticmethod
    def authenticate_by_pin(
        db: Session,
        pin: str,
        client_ip: str
    ) -> Tuple[Optional[TokenResponse], Optional[str]]:
        """Authenticate user with PIN code (optimized for mobile/car/TV)."""
        locked, _remaining = limiter.is_locked_out(client_ip)
        if locked:
            logger.warning("PIN login blocked (IP lockout): ip=%s", client_ip)
            return None, "Too many failed attempts. Try again later."

        # Admin user is the default PIN owner
        user = db.execute(select(AppUser).where(AppUser.username == "admin")).scalar_one_or_none()
        if not user or not user.is_active or not user.pin_hash:
            # Burn a dummy verify to equalize timing
            verify_pin(pin, timing_equalizer_hash())
            limiter.record_failure(client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
            logger.warning("PIN login failed (not configured): ip=%s", client_ip)
            return None, "PIN authentication not configured."

        # Per-account lockout, same policy as the password flow
        if user.locked_until and user.locked_until > utcnow():
            logger.warning("PIN login blocked (account lockout): ip=%s user=%r", client_ip, user.username)
            return None, "Account temporarily locked. Try again later."

        if not verify_pin(pin, user.pin_hash):
            limiter.record_failure(client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
                user.locked_until = utcnow() + timedelta(minutes=settings.LOCKOUT_MINUTES)
                logger.warning(
                    "Account locked after %d failed PIN attempts: ip=%s user=%r",
                    user.failed_login_attempts, client_ip, user.username,
                )
            db.commit()
            invalidate_user(user.username)
            logger.warning(
                "Failed login (bad PIN): ip=%s user=%r attempts=%d",
                client_ip, user.username, user.failed_login_attempts,
            )
            return None, "Incorrect PIN code."

        # Upgrade legacy SHA-256 PIN hashes to bcrypt on successful login
        if is_legacy_pin_hash(user.pin_hash):
            user.pin_hash = hash_pin(pin)
            logger.info("Upgraded legacy PIN hash to bcrypt: user=%r", user.username)

        limiter.record_success(client_ip)
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()
        invalidate_user(user.username)

        logger.info("Successful login (PIN): ip=%s user=%r", client_ip, user.username)
        token_str, _, expire_dt = create_access_token(
            subject=user.username,
            claims={"uid": user.id, "tv": user.token_version or 0, "auth_mode": "pin"}
        )
        return TokenResponse(
            access_token=token_str,
            token_type="bearer",
            expires_in=int((expire_dt - utcnow()).total_seconds())
        ), None

    @staticmethod
    def change_password(db: Session, user: AppUser, new_password: str):
        """Set a new bcrypt password and revoke every previously issued token."""
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
    def change_pin(db: Session, user: AppUser, new_pin: str):
        """Update user PIN code with strong hashing and revoke old sessions."""
        target = get_attached_user(db, user)
        if target is None:
            raise ValueError("User not found.")
        target.pin_hash = hash_pin(new_pin)
        target.token_version = (target.token_version or 0) + 1
        target.updated_at = utcnow()
        db.commit()
        invalidate_user(target.username)
        logger.info("PIN changed, sessions revoked: user=%r", target.username)
