"""
T-Music Subsonic credentials helper for the web UI (JWT session, not Subsonic auth).

SECURITY (2026-10-11): despite the file name this is a SELF-SERVICE endpoint —
every authenticated user may mint/rotate their *own* Subsonic token. That is
intentional and not a privilege escalation: /rest accepts any active account
and the library is shared, exactly as it is over /api. It was gated on
`get_current_user` while living in a file called "subsonic_admin_router", which
read like a bug; the naming is now explicit and rotation is rate-limited so a
token cannot be churned (every rotation invalidates the previous one).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.rate_limiter import limiter
from app.models.auth import AppUser
from app.services import subsonic_auth

router = APIRouter(prefix="/api/subsonic", tags=["subsonic-credentials"])

# Rotation invalidates the previous token, so it doubles as a small DoZ vector
# against a user's own players.
ROTATE_PER_MIN = 5


@router.get("/credentials")
async def get_credentials(db: Session = Depends(get_db), user: AppUser = Depends(get_current_user)):
    """Return connection hints + mint/read the caller's own Subsonic token."""
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
    """Issue a fresh Subsonic token for the caller (invalidates the previous one)."""
    if limiter.is_rate_limited(f"subsonic_rotate:{user.id}", ROTATE_PER_MIN, 60):
        raise HTTPException(
            status_code=429,
            detail="Too many credential rotations. Slow down.",
            headers={"Retry-After": "60"},
        )
    token = subsonic_auth.ensure_subsonic_token(db, user, rotate=True)
    return {"username": user.username, "token": token}
