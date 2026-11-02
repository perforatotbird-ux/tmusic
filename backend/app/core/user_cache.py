"""
T-Music user lookup cache
Short-lived in-process cache for AppUser auth checks (hot path on every request).
Caches a detached snapshot; mutations must re-bind via get_attached_user / db.get.
"""
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import AppUser
from app.utils.time import utcnow

_TTL = timedelta(seconds=30)
_cache: Dict[str, Tuple[AppUser, datetime]] = {}
# Read from the event loop (auth dependency) and written from worker threads
# (invalidate_user via run_password_work), so guard the dict explicitly rather
# than relying on the GIL.
_lock = threading.Lock()


def get_user_by_username(db: Session, username: str) -> Optional[AppUser]:
    """Return AppUser snapshot for auth checks. May be detached from db."""
    now = utcnow()
    with _lock:
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
    with _lock:
        _cache[username] = (user, now + _TTL)
    return user


def get_attached_user(db: Session, user: AppUser) -> Optional[AppUser]:
    """Session-bound copy of user (required for updates)."""
    if user in db:
        return user
    return db.get(AppUser, user.id)


def invalidate_user(username: Optional[str] = None) -> None:
    with _lock:
        if username is None:
            _cache.clear()
        else:
            _cache.pop(username, None)
