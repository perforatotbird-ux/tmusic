"""
T-Music Authentication API Router
Endpoints for username/password login, profile status, and password updates.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.responses import Response as RawResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.password_work import run_password_work
from app.core.csrf import new_csrf_token, csrf_cookie_kwargs, CSRF_COOKIE
from app.core.database import get_db
from app.api.dependencies import get_current_user, security_bearer
from app.core.token_store import revoke_access_token
from app.models.auth import AppUser
from app.schemas.auth import (
    LoginRequest, TokenResponse, UserResponse,
    ChangePasswordRequest,
)
from app.services.auth_service import AuthService
from app.services.cover_cache_service import cover_cache, is_safe_host, MAX_IMAGE_BYTES
from app.utils.client_ip import get_client_ip
from app.utils.http import cover_cache_headers
from app.utils.time import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Same bound as library cover uploads: cap RAM buffering, real check after decode.
MAX_AVATAR_UPLOAD_BYTES = MAX_IMAGE_BYTES + 1


def _avatar_url(user: AppUser) -> Optional[str]:
    if user.avatar_key:
        return f"/api/auth/avatar?v={user.avatar_version or 0}"
    return None


def _set_session_cookie(response: Response, token_resp: TokenResponse) -> None:
    response.set_cookie(
        key="access_token",
        value=token_resp.access_token,
        max_age=token_resp.expires_in,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    # Double-submit CSRF token (readable cookie, echoed in X-CSRF-Token header).
    response.set_cookie(
        value=new_csrf_token(),
        max_age=token_resp.expires_in,
        **csrf_cookie_kwargs(),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Authenticate with username and password."""
    client_ip = get_client_ip(request)
    token_resp, err = await run_password_work(AuthService.authenticate_by_password, db, body.username, body.password, client_ip)
    if err or not token_resp:
        raise HTTPException(status_code=401, detail=err or "Authentication failed.")

    _set_session_cookie(response, token_resp)
    return token_resp


@router.get("/csrf")
async def refresh_csrf(response: Response):
    """Re-arm the CSRF double-submit cookie.

    Called by the SPA when a mutation fails with code `csrf_cookie_missing`
    (fail-closed CSRF — see app.core.csrf): sets a fresh readable csrf_token
    cookie so the retried mutation can echo it in X-CSRF-Token. Unauthenticated
    and side-effect free by design: the token is random per call and proves
    nothing by itself — the defense is the *pair* (cookie + echoed header).
    """
    response.set_cookie(
        value=new_csrf_token(),
        max_age=60 * 60 * 24,
        **csrf_cookie_kwargs(),
    )
    return {"status": "ok"}


@router.get("/me", response_model=UserResponse)
async def get_me(request: Request, user: AppUser = Depends(get_current_user)):
    """Get currently authenticated user status."""
    return UserResponse(
        id=user.id,
        username=user.username,
        is_active=user.is_active,
        role=getattr(user, "role", "user") or "user",
        avatar_url=_avatar_url(user),
        password_change_required=getattr(request.state, "password_change_required", False),
    )


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, user: AppUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Change account password (requires current password)."""
    changed = await run_password_work(AuthService.change_password_with_current, db, user,
                                      body.current_password, body.new_password)
    if not changed:
        raise HTTPException(status_code=400, detail="Current password incorrect.")
    return {"status": "ok", "message": "Password updated successfully."}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    db: Session = Depends(get_db),
):
    """Revoke JWT server-side and clear session cookie."""
    token = None
    if credentials:
        token = credentials.credentials
    elif "access_token" in request.cookies:
        token = request.cookies.get("access_token")
    if token:
        revoke_access_token(db, token)
    response.delete_cookie(key="access_token")
    response.delete_cookie(key=CSRF_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/avatar")
async def get_avatar(user: AppUser = Depends(get_current_user)):
    """Serve the current user's profile avatar (custom-cover store)."""
    if not user.avatar_key:
        raise HTTPException(status_code=404, detail="No avatar set.")
    cached = await run_in_threadpool(
        cover_cache.get_cached_item, user.avatar_key, True
    )
    if cached:
        data, mime = cached
        return RawResponse(content=data, media_type=mime, headers=cover_cache_headers(user.avatar_key))
    path = await run_in_threadpool(
        cover_cache.get_cached_path, user.avatar_key, True
    )
    if path:
        from fastapi.responses import FileResponse
        return FileResponse(path, headers=cover_cache_headers(user.avatar_key))
    raise HTTPException(status_code=404, detail="Avatar not found.")


@router.post("/avatar")
async def set_avatar(
    request: Request,
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Set profile avatar from an uploaded file or an image URL."""
    if file:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > MAX_AVATAR_UPLOAD_BYTES + 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large.")
            except ValueError:
                pass
        try:
            content = await file.read(MAX_AVATAR_UPLOAD_BYTES)
        finally:
            await file.close()
        if len(content) >= MAX_AVATAR_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Uploaded file is too large.")
        key = await run_in_threadpool(cover_cache.save_custom_cover, content)
        if not key:
            raise HTTPException(status_code=400, detail="Invalid image file or unsupported format.")
    elif url:
        if not await is_safe_host(url):
            raise HTTPException(status_code=400, detail="Restricted or invalid image URL.")
        data, _ = await cover_cache.fetch_and_cache(url)
        if not data:
            raise HTTPException(status_code=502, detail="Failed to download image from provided URL.")
        key = await run_in_threadpool(cover_cache.save_custom_cover, data)
    else:
        raise HTTPException(status_code=400, detail="Provide either an image file or a valid URL.")

    user.avatar_key = key
    user.avatar_version = (user.avatar_version or 0) + 1
    user.updated_at = utcnow()
    db.commit()
    return {"status": "ok", "avatar_url": _avatar_url(user)}


@router.delete("/avatar")
async def reset_avatar(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Remove the profile avatar (falls back to the letter badge)."""
    user.avatar_key = None
    user.avatar_version = (user.avatar_version or 0) + 1
    user.updated_at = utcnow()
    db.commit()
    return {"status": "ok"}
