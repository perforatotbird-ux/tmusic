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

    # Tokens passed via URL query (?token=) end up in proxy/browser logs, so they
    # must be short-lived single-purpose tokens — never a full session JWT.
    if token_source == "query" and query_scope is not None:
        if payload.get("scope") != query_scope:
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
    """Validate user for audio streaming: supports Authorization header, cookie, or ?token= parameter for native HTML5 <audio>.

    The ?token= path accepts ONLY short-lived scope="stream" tokens minted via
    POST /api/stream/token — a full session JWT in the URL is rejected because
    URLs leak into proxy logs, browser history, and Referer headers.
    """
    return _authenticate_request(
        request,
        credentials,
        db,
        query_token=token,
        query_scope="stream",
        missing_detail="Authentication required for stream playback.",
        invalid_token_detail="Invalid or expired stream token.",
        inactive_detail="User account inactive.",
    )
