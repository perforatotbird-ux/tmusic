"""
T-Music Authentication API Router
Endpoints for username/password login, profile status, and password updates.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.config import settings
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
from app.utils.client_ip import get_client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
    token_resp, err = AuthService.authenticate_by_password(db, body.username, body.password, client_ip)
    if err or not token_resp:
        raise HTTPException(status_code=401, detail=err or "Authentication failed.")

    _set_session_cookie(response, token_resp)
    return token_resp


@router.get("/me", response_model=UserResponse)
async def get_me(user: AppUser = Depends(get_current_user)):
    """Get currently authenticated user status."""
    return UserResponse(id=user.id, username=user.username, is_active=user.is_active)


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, user: AppUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Change account password (requires current password)."""
    from app.core.security import verify_password
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password incorrect.")

    AuthService.change_password(db, user, body.new_password)
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
