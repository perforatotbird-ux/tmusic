"""
T-Music API Dependencies
Authentication, database injection, and per-request security checks.
"""
from typing import Optional
from fastapi import Depends, HTTPException, Request, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.core.token_store import is_token_revoked
from app.core.user_cache import get_user_by_username
from app.models.auth import AppUser

security_bearer = HTTPBearer(auto_error=False)

ROLE_PERMISSIONS = {
    "admin": frozenset({
        "admin.access",
        "admin.audit.view",
        "admin.users.manage",
        "admin.settings.manage",
        "admin.db.manage",
        "admin.backups.manage",
        "catalog.manage",
    }),
    "editor": frozenset({"catalog.manage"}),
    "user": frozenset(),
}


def user_permissions(user: AppUser) -> frozenset:
    role = (getattr(user, "role", None) or "user") if user else "user"
    if role not in ROLE_PERMISSIONS:
        return frozenset()
    return ROLE_PERMISSIONS[role]


def user_has_permission(user: AppUser, permission: str) -> bool:
    if not user:
        return False
    if user.username == "admin":
        return True
    return permission in user_permissions(user)


def _extract_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
    query_token: Optional[str] = None,
) -> Optional[str]:
    if credentials:
        return credentials.credentials
    if query_token:
        return query_token
    if "access_token" in request.cookies:
        return request.cookies.get("access_token")
    return None


def _authenticate_request(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
    db: Session,
    query_token: Optional[str] = None,
    invalid_token_detail: str = "Invalid or expired token.",
    missing_detail: str = "Authentication required.",
    inactive_detail: str = "User account is inactive.",
    query_scope: Optional[str] = None,
    required_scope: Optional[str] = None,
    allow_scoped_token: bool = False,
) -> AppUser:
    if not settings.REQUIRE_AUTH:
        user = get_user_by_username(db, "admin")
        if user:
            return user

    token_source = "header" if credentials else ("query" if query_token else "cookie")
    token = _extract_token(request, credentials, query_token)
    if not token:
        raise HTTPException(status_code=401, detail=missing_detail)

    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail=invalid_token_detail)

    token_scope = payload.get("scope")
    if required_scope is not None and token_scope != required_scope:
        raise HTTPException(status_code=401, detail=invalid_token_detail)
    if required_scope is None and token_scope is not None and not allow_scoped_token:
        raise HTTPException(status_code=401, detail=invalid_token_detail)
    if token_source == "query" and query_scope is not None and token_scope != query_scope:
        raise HTTPException(status_code=401, detail=invalid_token_detail)

    if is_token_revoked(db, payload.get("jti", "")):
        raise HTTPException(status_code=401, detail=invalid_token_detail)

    username = payload["sub"]
    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail=inactive_detail)

    # Global session revocation: password change bumps token_version,
    # invalidating every token issued before the change (defaults to 0 for
    # tokens minted before this claim existed).
    if payload.get("tv", 0) != (user.token_version or 0):
        raise HTTPException(status_code=401, detail=invalid_token_detail)

    return user


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    db: Session = Depends(get_db)
) -> AppUser:
    """Validate bearer token from Authorization header or cookie."""
    return _authenticate_request(
        request,
        credentials,
        db,
        missing_detail="Authentication required.",
        invalid_token_detail="Invalid or expired token.",
        inactive_detail="User account is inactive.",
    )


async def get_stream_user(
    request: Request,
    token: Optional[str] = Query(None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    db: Session = Depends(get_db)
) -> AppUser:
    """Validate user for audio streaming with header, cookie, or scoped query auth.

    Query authentication accepts only short-lived scope="stream" tokens minted via
    POST /api/stream/token. Header and cookie authentication may use a normal
    session JWT or a scoped stream token.
    """
    return _authenticate_request(
        request,
        credentials,
        db,
        query_token=token,
        query_scope="stream",
        required_scope=None,
        allow_scoped_token=True,
        missing_detail="Authentication required for stream playback.",
        invalid_token_detail="Invalid or expired stream token.",
        inactive_detail="User account inactive.",
    )


def user_is_admin(user: AppUser) -> bool:
    """RBAC gate: role=admin OR legacy username 'admin' (same password until roles are split)."""
    if not user:
        return False
    role = getattr(user, "role", None) or "user"
    return role == "admin" or user.username == "admin"


async def get_catalog_user(
    user: AppUser = Depends(get_current_user),
) -> AppUser:
    if not user_has_permission(user, "catalog.manage"):
        raise HTTPException(status_code=403, detail="Catalog management permission required.")
    return user


async def get_admin_user(
    user: AppUser = Depends(get_current_user),
) -> AppUser:
    if not user_has_permission(user, "admin.access"):
        raise HTTPException(status_code=403, detail="Administrator role required.")
    return user


async def get_backup_admin(
    user: AppUser = Depends(get_current_user),
) -> AppUser:
    if not user_has_permission(user, "admin.backups.manage"):
        raise HTTPException(status_code=403, detail="Backup management permission required.")
    return user
