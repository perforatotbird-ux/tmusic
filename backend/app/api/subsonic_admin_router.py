"""
T-Music Subsonic credentials helper for the web UI (JWT session, not Subsonic auth).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.auth import AppUser
from app.services import subsonic_auth

router = APIRouter(prefix="/api/subsonic", tags=["subsonic-admin"])


@router.get("/credentials")
async def get_credentials(db: Session = Depends(get_db), user: AppUser = Depends(get_current_user)):
    """Return connection hints + mint/read Subsonic token for this user."""
    token = subsonic_auth.ensure_subsonic_token(db, user)
    return {
        "username": user.username,
        "token": token,
        "server_path": "/rest",
        "auth_modes": [
            "password = Subsonic token (p=...)",
            "token auth t=md5(token+salt)&s=salt",
        ],
        "api_version": "1.16.1",
        "example_ping": "/rest/ping.view?u=<user>&p=<token>&v=1.16.1&c=T-Music&f=json",
    }


@router.post("/credentials/rotate")
async def rotate_credentials(db: Session = Depends(get_db), user: AppUser = Depends(get_current_user)):
    """Issue a fresh Subsonic token (invalidates the previous one)."""
    user.subsonic_token_enc = None
    db.commit()
    token = subsonic_auth.ensure_subsonic_token(db, user)
    return {"username": user.username, "token": token}
