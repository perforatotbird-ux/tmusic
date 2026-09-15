"""
T-Music JWT revocation store
Persists revoked token IDs (jti) so logout actually invalidates access tokens.
"""
import time
from datetime import datetime
from typing import Dict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token, token_expiry_from_payload
from app.models.auth import RevokedToken

# Negative cache: jti -> timestamp until which "not revoked" is trusted.
# Every authenticated request hits is_token_revoked, so caching the common
# (False) outcome removes one indexed DB round-trip per request.
# TRADEOFF: a logout takes effect up to NEGATIVE_CACHE_TTL seconds later for
# already-authenticated in-flight requests. revoke_access_token() drops the
# entry, so the revoking session itself is cut immediately; only OTHER
# processes/hosts (multi-worker) may lag by TTL. Same single-process caveat
# as the in-memory rate limiter — use Redis for multi-worker deployments.
NEGATIVE_CACHE_TTL = 60.0
_revoked_negative: Dict[str, float] = {}


def revoke_access_token(db: Session, token: str) -> bool:
    """Blacklist a JWT by jti. Returns True if a jti was recorded."""
    payload = decode_access_token(token)
    if not payload or "jti" not in payload:
        return False
    jti = payload["jti"]
    _revoked_negative.pop(jti, None)
    existing = db.execute(
        select(RevokedToken.id).where(RevokedToken.jti == jti)
    ).scalar()
    if existing is not None:
        return True
    db.add(RevokedToken(jti=jti, expires_at=token_expiry_from_payload(payload)))
    db.commit()
    # Opportunistic purge: expired rows are useless for future checks, so drop
    # them whenever we are already writing (keeps the table bounded even on
    # servers with months of uptime between restarts).
    try:
        purge_expired_revocations(db)
    except Exception:
        pass
    return True


def is_token_revoked(db: Session, jti: str) -> bool:
    if not jti:
        return False
    now = time.time()
    if _revoked_negative.get(jti, 0.0) > now:
        return False
    hit = db.execute(
        select(RevokedToken.id).where(RevokedToken.jti == jti)
    ).scalar() is not None
    if not hit:
        _revoked_negative[jti] = now + NEGATIVE_CACHE_TTL
        # Bound the cache itself: drop stale entries when it grows large.
        if len(_revoked_negative) > 50_000:
            for k in [k for k, exp in _revoked_negative.items() if exp <= now]:
                del _revoked_negative[k]
    return hit


def purge_expired_revocations(db: Session) -> int:
    """Delete revocations whose tokens already expired. Returns rows removed."""
    n = db.execute(
        delete(RevokedToken).where(RevokedToken.expires_at < datetime.utcnow())
    ).rowcount or 0
    db.commit()
    return n
