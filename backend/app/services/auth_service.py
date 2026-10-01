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
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    verify_password, hash_password,
    create_access_token,
    timing_equalizer_hash, password_needs_rehash, password_change_required,
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
    def _ip_gate(client_ip: str, username: str = "-") -> Optional[str]:
        """IP-level lockout check. Returns a generic error message or None."""
        locked, _remaining = limiter.is_locked_out(client_ip)
        if locked:
            logger.warning("Login blocked (IP lockout): ip=%s user=%r", client_ip, username)
            return "Too many failed attempts. Try again later."
        return None

    @staticmethod
    def _account_gate(user: AppUser, client_ip: str) -> Optional[str]:
        """Per-account lockout check. Returns a generic error message or None."""
        if user.locked_until and user.locked_until > utcnow():
            logger.warning("Login blocked (account lockout): ip=%s user=%r", client_ip, user.username)
            return "Account temporarily locked. Try again later."
        return None

    @staticmethod
    def _register_failure(db: Session, user: AppUser, client_ip: str, what: str) -> None:
        """Record a failed attempt on both IP and account counters."""
        limiter.record_failure(
            client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = utcnow() + timedelta(minutes=settings.LOCKOUT_MINUTES)
            logger.warning(
                "Account locked after %d failed attempts: ip=%s user=%r",
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
                        claims: dict, kind: str) -> Tuple[TokenResponse, None]:
        """Reset counters and mint a session token (shared tail of all flows)."""
        limiter.record_success(client_ip)
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
        ), None

    @staticmethod
    def authenticate_by_password(
        db: Session,
        username: str,
        password: str,
        client_ip: str
    ) -> Tuple[Optional[TokenResponse], Optional[str]]:
        """Authenticate user with username and password with brute-force lockout."""
        # 1. Check IP lockout (generic message — exact countdown stays server-side)
        if err := AuthService._ip_gate(client_ip, username):
            return None, err

        # 2. Find user
        user = db.execute(select(AppUser).where(AppUser.username == username).with_for_update()).scalar_one_or_none()
        if not user or not user.is_active:
            # Burn a dummy Argon2id verify so timing does not reveal account existence
            verify_password(password, timing_equalizer_hash())
            limiter.record_failure(client_ip, settings.MAX_FAILED_LOGIN_ATTEMPTS, settings.LOCKOUT_MINUTES * 60)
            logger.warning("Failed login (unknown/inactive user): ip=%s user=%r", client_ip, username)
            return None, "Invalid credentials."

        # 3. Check account lockout (generic message — countdown stays server-side)
        if err := AuthService._account_gate(user, client_ip):
            return None, err

        # 4. Verify password
        if not verify_password(password, user.password_hash):
            AuthService._register_failure(db, user, client_ip, "bad password")
            return None, "Invalid credentials."

        # Rehash only an unambiguous password. Row lock prevents a concurrent
        # password reset from being overwritten by opportunistic migration.
        must_change = password_change_required(password, user.password_hash)
        if not must_change and password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            logger.info("Upgraded password hash to Argon2id: user=%r", username)

        claims = {"uid": user.id, "tv": user.token_version or 0}
        if must_change:
            claims["pwd_reset"] = True
        return AuthService._finish_success(db, user, client_ip, claims, "password")

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
