"""
T-Music user lookup cache
Short-lived in-process cache for AppUser auth checks (hot path on every request).
Caches a detached snapshot; mutations must re-bind via get_attached_user / db.get.
"""
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import AppUser

_TTL = timedelta(seconds=30)
_cache: Dict[str, Tuple[AppUser, datetime]] = {}


def get_user_by_username(db: Session, username: str) -> Optional[AppUser]:
    """Return AppUser snapshot for auth checks. May be detached from db."""
    now = datetime.utcnow()
    hit = _cache.get(username)
    if hit is not None:
        user, expires = hit
        if expires > now:
            return user
        _cache.pop(username, None)

    user = db.execute(
        select(AppUser).where(AppUser.username == username)
    ).scalar_one_or_none()
    if user is None:
        return None

    # Detach so the cached object is independent of the request session lifecycle.
    db.expunge(user)
    _cache[username] = (user, now + _TTL)
    return user


def get_attached_user(db: Session, user: AppUser) -> Optional[AppUser]:
    """Session-bound copy of user (required for updates)."""
    if user in db:
        return user
    return db.get(AppUser, user.id)


def invalidate_user(username: Optional[str] = None) -> None:
    if username is None:
        _cache.clear()
    else:
        _cache.pop(username, None)
